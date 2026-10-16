from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from game_engine.contracts import (
	NLH_GAME_ID,
	EngineTransition,
	HandOutcome,
	PlayerAward,
	PlayerSeed,
	PokerAction,
	PokerActionKind,
	PokerEngine,
	PokerEvent,
	PokerEventKind,
		PokerGameMode,
	PokerTableConfig,
)
from game_engine.deck import Deck, ShuffleSource
from game_engine.hand_evaluator import evaluate_hand
from game_engine.nlh_state import (
	NLHHandState,
	NLHPlayerState,
	NLHStreet,
	advance_nlh_street,
	begin_nlh_showdown,
	initialize_nlh_hand,
)
from game_engine.pots import build_side_pots, resolve_pot_awards


class NLHEngine(PokerEngine[NLHHandState]):
	game_id = NLH_GAME_ID

	def create_state(
		self,
		config: PokerTableConfig,
		players: Sequence[PlayerSeed],
		*,
		table_id: str,
		hand_id: str,
		button_seat: int,
		straddle_player_id: str | None = None,
		shuffle_source: ShuffleSource,
	) -> NLHHandState:
		return start_nlh_hand(
			table_id,
			hand_id,
			config,
			tuple(players),
			button_seat,
			Deck(shuffle_source),
			straddle_player_id,
		).state

	def legal_actions(
		self, state: NLHHandState, player_id: str
	) -> tuple[PokerAction, ...]:
		return legal_nlh_actions(state, player_id)

	def apply_action(
		self, state: NLHHandState, action: PokerAction
	) -> EngineTransition[NLHHandState]:
		return apply_nlh_action(state, action)


def start_nlh_hand(
	table_id: str,
	hand_id: str,
	config: PokerTableConfig,
	players: tuple[PlayerSeed, ...],
	button_seat: int,
	deck: Deck,
	straddle_player_id: str | None = None,
) -> EngineTransition[NLHHandState]:
	if len(players) > config.max_seats:
		raise ValueError("Players exceed the configured table capacity.")
	if len({player.player_id for player in players}) != len(players):
		raise ValueError("Player IDs must be unique in a hand.")
	if len({player.seat_index for player in players}) != len(players):
		raise ValueError("Seat indices must be unique in a hand.")
	all_seats = sorted(player.seat_index for player in players)
	if any(seat >= config.max_seats for seat in all_seats):
		raise ValueError("Player seat is outside the configured table capacity.")
	if not 0 <= button_seat < config.max_seats:
		raise ValueError("Button seat is outside the configured table capacity.")
	candidate_small_blind_seat = (
		button_seat
		if len(all_seats) == 2 and button_seat in all_seats
		else _next_seat(all_seats, button_seat)
	)
	entry_post_ids = {
		player.player_id
		for player in players
		if player.new_to_table
		and player.accept_big_blind_post
		and player.seat_index != candidate_small_blind_seat
	}
	waiting_players = tuple(
		player
		for player in players
		if player.new_to_table
		and not player.accept_big_blind_post
		and player.seat_index != candidate_small_blind_seat
	)
	eligible_players = tuple(
		player
		for player in players
		if not player.new_to_table
		or player.seat_index == candidate_small_blind_seat
		or player.accept_big_blind_post
	)
	state = initialize_nlh_hand(
		table_id,
		hand_id,
		config,
		eligible_players,
		button_seat,
		deck,
		allow_unoccupied_button=True,
	)
	ordered_seats = sorted(player.seat_index for player in state.players)
	if len(ordered_seats) == 2 and button_seat in ordered_seats:
		small_blind_seat = button_seat
		big_blind_seat = _next_seat(ordered_seats, small_blind_seat)
	else:
		small_blind_seat = _next_seat(ordered_seats, button_seat)
		big_blind_seat = _next_seat(ordered_seats, small_blind_seat)
	straddler_seat: int | None = None
	if straddle_player_id is not None:
		if config.game_mode is PokerGameMode.TOURNAMENT:
			raise ValueError("Straddles are not allowed in tournament hands.")
		if len(ordered_seats) < 3:
			raise ValueError("A live UTG straddle requires at least three players.")
		straddler = next(
			(player for player in state.players if player.player_id == straddle_player_id),
			None,
		)
		if straddler is None:
			raise ValueError("The straddler must be seated in this hand.")
		straddler_seat = _next_seat(ordered_seats, big_blind_seat)
		if straddler.seat_index != straddler_seat:
			raise ValueError("Only the player immediately after the big blind may straddle.")
		if straddler.stack < 2 * config.big_blind:
			raise ValueError("A straddle requires at least twice the big blind in chips.")

	players_by_seat = {player.seat_index: player for player in state.players}
	events: list[PokerEvent] = [
		PokerEvent(
			PokerEventKind.NEW_PLAYER_WAITING_FOR_BIG_BLIND,
			player.player_id,
		)
		for player in waiting_players
	]
	events.extend([
		PokerEvent(
			PokerEventKind.CARDS_DEALT,
			player.player_id,
			cards=player.hole_cards,
		)
		for player in state.players
	])
	for seat, blind, event_kind in (
		(small_blind_seat, config.small_blind, PokerEventKind.SMALL_BLIND_POSTED),
		(big_blind_seat, config.big_blind, PokerEventKind.BIG_BLIND_POSTED),
	):
		player = players_by_seat[seat]
		posted = min(player.stack, blind)
		players_by_seat[seat] = replace(
			player,
			stack=player.stack - posted,
			street_contribution=posted,
			total_contribution=posted,
			all_in=player.stack == posted,
		)
		events.append(PokerEvent(event_kind, player.player_id, amount=posted))
		if players_by_seat[seat].all_in:
			events.append(
				PokerEvent(PokerEventKind.PLAYER_WENT_ALL_IN, player.player_id, amount=posted)
			)
	if straddler_seat is not None:
		straddler = players_by_seat[straddler_seat]
		straddle_amount = 2 * config.big_blind
		players_by_seat[straddler_seat] = replace(
			straddler,
			stack=straddler.stack - straddle_amount,
			street_contribution=straddler.street_contribution + straddle_amount,
			total_contribution=straddler.total_contribution + straddle_amount,
			all_in=straddler.stack == straddle_amount,
		)
		events.append(
			PokerEvent(
				PokerEventKind.STRADDLE_POSTED,
				straddler.player_id,
				amount=straddle_amount,
			)
		)
		if players_by_seat[straddler_seat].all_in:
			events.append(
				PokerEvent(
					PokerEventKind.PLAYER_WENT_ALL_IN,
					straddler.player_id,
					amount=straddle_amount,
				)
		)

	for player_id in sorted(entry_post_ids):
		player = _player_by_id(state, player_id)
		if player.seat_index == small_blind_seat:
			continue
		current = players_by_seat[player.seat_index]
		if player.seat_index == big_blind_seat:
			posted = current.street_contribution
		else:
			posted = min(current.stack, config.big_blind)
			players_by_seat[player.seat_index] = replace(
				current,
				stack=current.stack - posted,
				street_contribution=current.street_contribution + posted,
				total_contribution=current.total_contribution + posted,
				all_in=current.stack == posted,
			)
		events.append(
			PokerEvent(
				PokerEventKind.NEW_PLAYER_POSTED_BIG_BLIND,
				player_id,
				amount=posted,
			)
		)
		if players_by_seat[player.seat_index].all_in:
			events.append(
				PokerEvent(
					PokerEventKind.PLAYER_WENT_ALL_IN,
					player_id,
					amount=posted,
				)
		)

	state = replace(
		state,
		players=tuple(players_by_seat[player.seat_index] for player in state.players),
		current_bet=max(player.street_contribution for player in players_by_seat.values()),
		minimum_raise=(
			2 * config.big_blind
			if straddler_seat is not None
			else config.big_blind
		),
	)
	first_actor = _next_seat(ordered_seats, straddler_seat) if straddler_seat is not None else (
		button_seat
		if len(ordered_seats) == 2
		else _next_seat(ordered_seats, big_blind_seat)
	)
	state = replace(
		state,
		actor_player_id=_next_actionable_player(state, first_actor, inclusive=True),
	)
	state, automatic_events, outcome = _continue_hand(state)
	events.extend(automatic_events)
	return EngineTransition(state, tuple(events), outcome)


def legal_nlh_actions(
	state: NLHHandState, player_id: str
) -> tuple[PokerAction, ...]:
	if state.street in {NLHStreet.COMPLETE, NLHStreet.SHOWDOWN}:
		return ()
	if state.actor_player_id != player_id:
		return ()
	player = _player_by_id(state, player_id)
	if player.folded or player.all_in:
		return ()

	actions = [PokerAction(player_id, PokerActionKind.FOLD)]
	to_call = max(0, state.current_bet - player.street_contribution)
	if to_call == 0:
		actions.append(PokerAction(player_id, PokerActionKind.CHECK))
	else:
		actions.append(PokerAction(player_id, PokerActionKind.CALL))

	can_raise = (
		player_id not in state.acted_player_ids
		or state.current_bet - player.last_action_bet >= state.minimum_raise
	)
	minimum_raise_to = state.current_bet + state.minimum_raise
	maximum_raise_to = player.street_contribution + player.stack
	if can_raise and maximum_raise_to >= minimum_raise_to:
		actions.append(
			PokerAction(player_id, PokerActionKind.RAISE, minimum_raise_to)
		)
	all_in_to = player.street_contribution + player.stack
	if player.stack > 0 and (
		all_in_to <= state.current_bet or can_raise
	):
		actions.append(PokerAction(player_id, PokerActionKind.ALL_IN))
	return tuple(actions)


def apply_nlh_action(
	state: NLHHandState, action: PokerAction
) -> EngineTransition[NLHHandState]:
	if state.street in {NLHStreet.COMPLETE, NLHStreet.SHOWDOWN}:
		raise ValueError("The hand is already complete.")
	if state.actor_player_id != action.player_id:
		raise ValueError("It is not this player's turn.")
	player = _player_by_id(state, action.player_id)
	if player.folded or player.all_in:
		raise ValueError("Folded or all-in players cannot act.")

	events: list[PokerEvent] = []
	acted = set(state.acted_player_ids)
	new_current_bet = state.current_bet
	new_minimum_raise = state.minimum_raise

	if action.kind is PokerActionKind.FOLD:
		player = replace(player, folded=True)
		events.append(PokerEvent(PokerEventKind.PLAYER_FOLDED, player.player_id))
	elif action.kind is PokerActionKind.CHECK:
		if player.street_contribution != state.current_bet:
			raise ValueError("Cannot check while facing a bet.")
		acted.add(player.player_id)
		events.append(PokerEvent(PokerEventKind.PLAYER_CHECKED, player.player_id))
	elif action.kind is PokerActionKind.CALL:
		to_call = state.current_bet - player.street_contribution
		if to_call <= 0:
			raise ValueError("Cannot call when no bet is owed; check instead.")
		paid = min(to_call, player.stack)
		player = _commit_chips(player, paid)
		acted.add(player.player_id)
		events.append(
			PokerEvent(PokerEventKind.PLAYER_CALLED, player.player_id, amount=paid)
		)
		if player.all_in:
			events.append(
				PokerEvent(PokerEventKind.PLAYER_WENT_ALL_IN, player.player_id, amount=paid)
			)
	elif action.kind is PokerActionKind.RAISE:
		if action.raise_to is None:
			raise ValueError("RAISE requires a raise-to total.")
		if (
			player.player_id in state.acted_player_ids
			and state.current_bet - player.last_action_bet < state.minimum_raise
		):
			raise ValueError("This player's raise has not been reopened.")
		minimum_raise_to = state.current_bet + state.minimum_raise
		maximum_raise_to = player.street_contribution + player.stack
		if not minimum_raise_to <= action.raise_to <= maximum_raise_to:
			raise ValueError("Raise-to amount is outside the legal range.")
		raise_size = action.raise_to - state.current_bet
		paid = action.raise_to - player.street_contribution
		player = _commit_chips(player, paid)
		new_current_bet = action.raise_to
		new_minimum_raise = raise_size
		acted = {player.player_id}
		events.append(
			PokerEvent(PokerEventKind.PLAYER_RAISED, player.player_id, amount=paid)
		)
		if player.all_in:
			events.append(
				PokerEvent(PokerEventKind.PLAYER_WENT_ALL_IN, player.player_id, amount=paid)
			)
	elif action.kind is PokerActionKind.ALL_IN:
		paid = player.stack
		target = player.street_contribution + paid
		if target > state.current_bet:
			raise_size = target - state.current_bet
			if (
				player.player_id in state.acted_player_ids
				and state.current_bet - player.last_action_bet < state.minimum_raise
			):
				raise ValueError("This player's raise has not been reopened.")
			new_current_bet = target
			if raise_size >= state.minimum_raise:
				new_minimum_raise = raise_size
				acted = {player.player_id}
		player = _commit_chips(player, paid)
		acted.add(player.player_id)
		events.append(
			PokerEvent(PokerEventKind.PLAYER_WENT_ALL_IN, player.player_id, amount=paid)
		)
	else:
		raise ValueError("Unsupported NLH action.")

	players = tuple(
		replace(player, last_action_bet=new_current_bet)
		if current.player_id == player.player_id
		else current
		for current in state.players
	)
	state = replace(
		state,
		players=players,
		current_bet=new_current_bet,
		minimum_raise=new_minimum_raise,
		acted_player_ids=frozenset(acted),
		actor_player_id=_next_actionable_player(state, player.seat_index),
	)
	state, progression_events, outcome = _continue_hand(state)
	events.extend(progression_events)
	return EngineTransition(state, tuple(events), outcome)


def _continue_hand(
	state: NLHHandState,
) -> tuple[NLHHandState, list[PokerEvent], HandOutcome | None]:
	events: list[PokerEvent] = []
	while True:
		live_players = [player for player in state.players if not player.folded]
		if len(live_players) == 1:
			state, outcome = _complete_by_fold(state, live_players[0].player_id)
			events.append(PokerEvent(PokerEventKind.HAND_COMPLETED))
			return state, events, outcome

		actionable = [player for player in live_players if not player.all_in]
		if len(actionable) == 1 and actionable[0].street_contribution < state.current_bet:
			state = replace(state, actor_player_id=actionable[0].player_id)
			return state, events, None
		if not actionable or len(actionable) == 1:
			if state.street < NLHStreet.RIVER:
				state = advance_nlh_street(state)
				events.extend(_street_events(state))
				state = _reset_betting_round(state)
				continue
			state = begin_nlh_showdown(state)
			state, outcome = _complete_at_showdown(state)
			events.append(PokerEvent(PokerEventKind.HAND_COMPLETED))
			return state, events, outcome

		if not _betting_round_complete(state, actionable):
			if state.actor_player_id not in {player.player_id for player in actionable}:
				state = replace(
					state,
					actor_player_id=_next_actionable_player(
						state,
						_player_by_id(state, state.actor_player_id).seat_index
						if state.actor_player_id is not None
						else state.button_seat,
					),
				)
			return state, events, None

		if state.street is NLHStreet.RIVER:
			state = begin_nlh_showdown(state)
			state, outcome = _complete_at_showdown(state)
			events.append(PokerEvent(PokerEventKind.HAND_COMPLETED))
			return state, events, outcome

		state = advance_nlh_street(state)
		events.extend(_street_events(state))
		state = _reset_betting_round(state)
		state = replace(
			state,
			actor_player_id=_next_actionable_player(
				state,
				state.button_seat,
				inclusive=False,
			),
		)
		return state, events, None


def _complete_by_fold(
	state: NLHHandState, winner_id: str
) -> tuple[NLHHandState, HandOutcome]:
	contributions = {
		player.player_id: player.total_contribution for player in state.players
	}
	result = build_side_pots(
		contributions,
		{player.player_id for player in state.players if player.folded},
	)
	awards = {winner_id: sum(pot.amount for pot in result.pots)}
	uncalled_returns = result.uncalled_returns
	outcome = _make_outcome(awards, uncalled_returns)
	state = _finish_state(state, awards, uncalled_returns, outcome, reveal=False)
	return state, outcome


def _complete_at_showdown(
	state: NLHHandState,
) -> tuple[NLHHandState, HandOutcome]:
	contributions = {
		player.player_id: player.total_contribution for player in state.players
	}
	folded = {player.player_id for player in state.players if player.folded}
	result = build_side_pots(contributions, folded)
	hand_ranks = {
		player.player_id: evaluate_hand((*player.hole_cards, *state.community_cards))
		for player in state.players
		if not player.folded
	}
	odd_chip_order = tuple(
		player.player_id
		for player in sorted(
			state.players,
			key=lambda item: (item.seat_index - state.button_seat - 1)
			% state.config.max_seats,
		)
	)
	awards = resolve_pot_awards(result.pots, hand_ranks, odd_chip_order)
	uncalled_returns = result.uncalled_returns
	outcome = _make_outcome(awards, uncalled_returns)
	state = _finish_state(state, awards, uncalled_returns, outcome, reveal=True)
	return state, outcome


def _make_outcome(
	awards: dict[str, int], uncalled_returns: dict[str, int]
) -> HandOutcome:
	return HandOutcome(
		total_pot=sum(awards.values()),
		gross_awards=tuple(
			PlayerAward(player_id, amount)
			for player_id, amount in sorted(awards.items())
			if amount > 0
		),
		uncalled_returns=tuple(
			PlayerAward(player_id, amount)
			for player_id, amount in sorted(uncalled_returns.items())
			if amount > 0
		),
	)


def _finish_state(
	state: NLHHandState,
	awards: dict[str, int],
	uncalled_returns: dict[str, int],
	outcome: HandOutcome,
	*,
	reveal: bool,
) -> NLHHandState:
	players = tuple(
		replace(
			player,
			stack=(
				player.stack
				+ awards.get(player.player_id, 0)
				+ uncalled_returns.get(player.player_id, 0)
			),
		)
		for player in state.players
	)
	return replace(
		state,
		players=players,
		street=NLHStreet.COMPLETE,
		actor_player_id=None,
		outcome=outcome,
		publicly_revealed_player_ids=(
			frozenset(player.player_id for player in players if not player.folded)
			if reveal
			else state.publicly_revealed_player_ids
		),
	)


def _street_events(state: NLHHandState) -> list[PokerEvent]:
	new_cards = {
		NLHStreet.FLOP: state.community_cards[-3:],
		NLHStreet.TURN: state.community_cards[-1:],
		NLHStreet.RIVER: state.community_cards[-1:],
	}.get(state.street, ())
	return [
		PokerEvent(
			PokerEventKind.BOARD_DEALT,
			cards=new_cards,
		),
		PokerEvent(
			PokerEventKind.STREET_CHANGED,
			amount=int(state.street),
		),
	]


def _reset_betting_round(state: NLHHandState) -> NLHHandState:
	players = tuple(
		replace(player, street_contribution=0) for player in state.players
	)
	return replace(
		state,
		players=players,
		current_bet=0,
		minimum_raise=state.config.big_blind,
		acted_player_ids=frozenset(),
		actor_player_id=None,
	)


def _betting_round_complete(
	state: NLHHandState, actionable: list[NLHPlayerState]
) -> bool:
	return all(
		player.player_id in state.acted_player_ids
		and player.street_contribution == state.current_bet
		for player in actionable
	)


def _commit_chips(player: NLHPlayerState, amount: int) -> NLHPlayerState:
	if amount <= 0 or amount > player.stack:
		raise ValueError("Committed chips must be positive and within the player's stack.")
	remaining = player.stack - amount
	return replace(
		player,
		stack=remaining,
		street_contribution=player.street_contribution + amount,
		total_contribution=player.total_contribution + amount,
		all_in=remaining == 0,
	)


def _next_actionable_player(
	state: NLHHandState, after_seat: int, *, inclusive: bool = False
) -> str | None:
	players_by_seat = {player.seat_index: player for player in state.players}
	for offset in range(0 if inclusive else 1, state.config.max_seats + 1):
		seat = (after_seat + offset) % state.config.max_seats
		player = players_by_seat.get(seat)
		if player is not None and not player.folded and not player.all_in:
			return player.player_id
	return None


def _next_seat(ordered_seats: list[int], seat: int) -> int:
	return next(
		(candidate for candidate in ordered_seats if candidate > seat),
		ordered_seats[0],
	)


def _player_by_id(state: NLHHandState, player_id: str) -> NLHPlayerState:
	for player in state.players:
		if player.player_id == player_id:
			return player
	raise ValueError("Player is not seated in this hand.")