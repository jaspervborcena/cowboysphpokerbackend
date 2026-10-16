from __future__ import annotations

import pytest
from random import Random

from game_engine import (
	Deck,
	NLHEngine,
	NLHStreet,
	PlayerSeed,
	PokerAction,
	PokerActionKind,
	PokerEventKind,
	PokerGameMode,
	PokerTableConfig,
	apply_nlh_action,
	legal_nlh_actions,
	start_nlh_hand,
)


def make_config(game_mode: PokerGameMode = PokerGameMode.RING) -> PokerTableConfig:
	return PokerTableConfig("no_limit_texas_holdem", 5, 10, 6, game_mode)


def start_hand(
	players: tuple[PlayerSeed, ...],
	button_seat: int,
	seed: int = 71,
	straddle_player_id: str | None = None,
	game_mode: PokerGameMode = PokerGameMode.RING,
):
	return start_nlh_hand(
		"table-rules",
		"hand-rules",
		make_config(game_mode),
		players,
		button_seat,
		Deck.for_testing(seed),
		straddle_player_id,
	)


def act(state, player_id: str, kind: PokerActionKind, raise_to: int | None = None):
	return apply_nlh_action(state, PokerAction(player_id, kind, raise_to))


def kinds_for(state, player_id: str) -> set[PokerActionKind]:
	return {action.kind for action in legal_nlh_actions(state, player_id)}


def test_heads_up_posts_button_small_blind_and_gives_button_first_action() -> None:
	transition = start_hand(
		(PlayerSeed("alice", 1, 100), PlayerSeed("bob", 4, 100)),
		button_seat=1,
	)
	state = transition.state
	players = {player.player_id: player for player in state.players}
	events = [event for event in transition.events if event.kind in {
		PokerEventKind.SMALL_BLIND_POSTED,
		PokerEventKind.BIG_BLIND_POSTED,
	}]

	assert [(event.player_id, event.amount) for event in events] == [
		("alice", 5),
		("bob", 10),
	]
	assert state.actor_player_id == "alice"
	assert state.current_bet == 10
	assert players["alice"].stack == 95
	assert players["bob"].stack == 90
	assert kinds_for(state, "alice") == {
		PokerActionKind.FOLD,
		PokerActionKind.CALL,
		PokerActionKind.RAISE,
		PokerActionKind.ALL_IN,
	}
	assert legal_nlh_actions(state, "bob") == ()


def test_engine_factory_preserves_supplied_table_hand_and_button() -> None:
	state = NLHEngine().create_state(
		make_config(),
		(PlayerSeed("alice", 1, 100), PlayerSeed("bob", 4, 100)),
		table_id="table-factory",
		hand_id="hand-factory",
		button_seat=4,
		shuffle_source=Random(76),
	)

	assert state.table_id == "table-factory"
	assert state.hand_id == "hand-factory"
	assert state.button_seat == 4
	assert state.actor_player_id == "bob"


def test_new_player_in_small_blind_seat_is_dealt_and_posts_natural_blind() -> None:
	transition = start_hand(
		(
			PlayerSeed("alice", 0, 100),
			PlayerSeed("new-player", 2, 100, new_to_table=True),
			PlayerSeed("bob", 4, 100),
		),
		button_seat=0,
	)
	state = transition.state
	new_player = next(
		player for player in state.players if player.player_id == "new-player"
	)
	blind_event = next(
		event
		for event in transition.events
		if event.kind is PokerEventKind.SMALL_BLIND_POSTED
	)

	assert state.button_seat == 0
	assert new_player.street_contribution == 5
	assert blind_event.player_id == "new-player"
	assert new_player.hole_cards == (
		state.deck.dealt_cards[0],
		state.deck.dealt_cards[3],
	)
	assert not any(
		event.kind is PokerEventKind.NEW_PLAYER_POSTED_BIG_BLIND
		for event in transition.events
	)


def test_new_player_outside_small_blind_waits_or_accepts_big_blind_post() -> None:
	players = (
		PlayerSeed("alice", 0, 100),
		PlayerSeed("bob", 2, 100),
		PlayerSeed("carol", 4, 100),
		PlayerSeed("new-player", 5, 100, new_to_table=True),
	)
	waiting = start_hand(players, button_seat=0)
	assert "new-player" not in {player.player_id for player in waiting.state.players}
	assert any(
		event.kind is PokerEventKind.NEW_PLAYER_WAITING_FOR_BIG_BLIND
		and event.player_id == "new-player"
		for event in waiting.events
	)
	assert len(waiting.state.deck.dealt_cards) == 6
	assert waiting.state.button_seat == 0

	accepted_players = (
		*players[:-1],
		PlayerSeed(
			"new-player",
			5,
			100,
			new_to_table=True,
			accept_big_blind_post=True,
		),
	)
	accepted = start_hand(accepted_players, button_seat=0)
	new_player = next(
		player for player in accepted.state.players if player.player_id == "new-player"
	)
	entry_post = next(
		event
		for event in accepted.events
		if event.kind is PokerEventKind.NEW_PLAYER_POSTED_BIG_BLIND
	)
	assert new_player.street_contribution == 10
	assert new_player.stack == 90
	assert entry_post.player_id == "new-player"
	assert entry_post.amount == 10
	assert accepted.state.button_seat == 0


def test_waiting_new_player_can_occupy_button_without_moving_it() -> None:
	transition = start_hand(
		(
			PlayerSeed("new-player", 0, 100, new_to_table=True),
			PlayerSeed("alice", 2, 100),
			PlayerSeed("bob", 4, 100),
		),
		button_seat=0,
	)

	assert transition.state.button_seat == 0
	assert {player.player_id for player in transition.state.players} == {"alice", "bob"}
	assert transition.state.actor_player_id == "alice"
	assert any(
		event.kind is PokerEventKind.NEW_PLAYER_WAITING_FOR_BIG_BLIND
		and event.player_id == "new-player"
		for event in transition.events
	)


def test_new_player_accepting_post_at_big_blind_is_not_charged_twice() -> None:
	transition = start_hand(
		(
			PlayerSeed("alice", 0, 100),
			PlayerSeed("bob", 2, 100),
			PlayerSeed(
				"new-player",
				4,
				100,
				new_to_table=True,
				accept_big_blind_post=True,
			),
		),
		button_seat=0,
	)
	new_player = next(
		player for player in transition.state.players if player.player_id == "new-player"
	)

	assert new_player.street_contribution == 10
	assert new_player.stack == 90
	assert sum(
		event.amount or 0
		for event in transition.events
		if event.player_id == "new-player"
		and event.kind is PokerEventKind.BIG_BLIND_POSTED
	) == 10
	assert sum(
		event.amount or 0
		for event in transition.events
		if event.player_id == "new-player"
		and event.kind is PokerEventKind.NEW_PLAYER_POSTED_BIG_BLIND
	) == 10


def test_ring_game_posts_one_live_utg_straddle_and_sets_action_order() -> None:
	transition = start_hand(
		(
			PlayerSeed("button", 0, 200),
			PlayerSeed("small-blind", 1, 200),
			PlayerSeed("big-blind", 2, 200),
			PlayerSeed("utg", 3, 200),
		),
		button_seat=0,
		straddle_player_id="utg",
	)
	state = transition.state
	straddler = next(player for player in state.players if player.player_id == "utg")
	straddle_events = [
		event for event in transition.events
		if event.kind is PokerEventKind.STRADDLE_POSTED
	]

	assert len(straddle_events) == 1
	assert straddle_events[0].player_id == "utg"
	assert straddle_events[0].amount == 20
	assert straddler.street_contribution == 20
	assert straddler.stack == 180
	assert state.current_bet == 20
	assert state.minimum_raise == 20
	assert state.actor_player_id == "button"
	assert PokerActionKind.RAISE in {
		action.kind for action in legal_nlh_actions(state, "button")
	}
	with pytest.raises(ValueError, match="legal range"):
		act(state, "button", PokerActionKind.RAISE, 30)
	assert act(state, "button", PokerActionKind.RAISE, 40).state.current_bet == 40


@pytest.mark.parametrize(
	("players", "button_seat", "straddler_id", "game_mode", "message"),
	[
		(
			(PlayerSeed("button", 0, 200), PlayerSeed("sb", 1, 200), PlayerSeed("bb", 2, 200)),
			0,
			"sb",
			PokerGameMode.RING,
			"immediately after the big blind",
		),
		(
			(PlayerSeed("button", 0, 200), PlayerSeed("bb", 1, 200)),
			0,
			"button",
			PokerGameMode.RING,
			"at least three players",
		),
		(
			(PlayerSeed("button", 0, 200), PlayerSeed("sb", 1, 200), PlayerSeed("bb", 2, 200), PlayerSeed("utg", 3, 200)),
			0,
			"utg",
			PokerGameMode.TOURNAMENT,
			"not allowed in tournament",
		),
		(
			(PlayerSeed("button", 0, 200), PlayerSeed("sb", 1, 200), PlayerSeed("bb", 2, 200), PlayerSeed("utg", 3, 19)),
			0,
			"utg",
			PokerGameMode.RING,
			"twice the big blind",
		),
	],
)
def test_straddle_rejects_invalid_position_mode_or_stack(
	players, button_seat, straddler_id, game_mode, message
) -> None:
	with pytest.raises(ValueError, match=message):
		start_hand(
			players,
			button_seat,
			straddle_player_id=straddler_id,
			game_mode=game_mode,
		)


def test_tournament_hand_uses_normal_blinds_without_straddle() -> None:
	state = start_hand(
		(
			PlayerSeed("button", 0, 200),
			PlayerSeed("small-blind", 1, 200),
			PlayerSeed("big-blind", 2, 200),
			PlayerSeed("utg", 3, 200),
		),
		button_seat=0,
		game_mode=PokerGameMode.TOURNAMENT,
	).state

	assert state.current_bet == 10
	assert state.actor_player_id == "utg"


def test_multiway_starts_left_of_big_blind_and_rejects_illegal_actions() -> None:
	state = start_hand(
		(
			PlayerSeed("alice", 0, 100),
			PlayerSeed("bob", 2, 100),
			PlayerSeed("carol", 5, 100),
		),
		button_seat=0,
	).state
	assert state.actor_player_id == "alice"

	with pytest.raises(ValueError, match="turn"):
		act(state, "bob", PokerActionKind.CALL)
	with pytest.raises(ValueError, match="Cannot check"):
		act(state, "alice", PokerActionKind.CHECK)
	with pytest.raises(ValueError, match="legal range"):
		act(state, "alice", PokerActionKind.RAISE, 15)


def test_full_raise_reopens_action_and_call_advances_to_flop() -> None:
	state = start_hand(
		(PlayerSeed("alice", 1, 100), PlayerSeed("bob", 4, 100)),
		button_seat=1,
	).state
	transition = act(state, "alice", PokerActionKind.RAISE, 30)
	assert transition.state.current_bet == 30
	assert transition.state.minimum_raise == 20
	assert transition.state.actor_player_id == "bob"
	assert transition.events[0].kind is PokerEventKind.PLAYER_RAISED

	state = act(transition.state, "bob", PokerActionKind.CALL).state
	assert state.street is NLHStreet.FLOP
	assert state.current_bet == 0
	assert state.minimum_raise == 10


def test_fold_completes_hand_and_awards_contested_blinds() -> None:
	state = start_hand(
		(PlayerSeed("alice", 1, 100), PlayerSeed("bob", 4, 100)),
		button_seat=1,
	).state
	state = act(state, "alice", PokerActionKind.FOLD).state

	assert state.street is NLHStreet.COMPLETE
	assert state.outcome is not None
	assert state.outcome.total_pot == 10
	assert state.outcome.gross_awards[0].player_id == "bob"
	assert state.outcome.gross_awards[0].amount == 10
	assert [(award.player_id, award.amount) for award in state.outcome.uncalled_returns] == [
		("bob", 5)
	]
	assert {player.player_id: player.stack for player in state.players} == {
		"alice": 95,
		"bob": 105,
	}


def test_called_down_hand_advances_streets_and_conserves_chips() -> None:
	state = start_hand(
		(PlayerSeed("alice", 1, 100), PlayerSeed("bob", 4, 100)),
		button_seat=1,
		seed=72,
	).state
	initial_total = sum(player.starting_stack for player in state.players)

	state = act(state, "alice", PokerActionKind.CALL).state
	state = act(state, "bob", PokerActionKind.CHECK).state
	assert state.street is NLHStreet.FLOP
	assert state.actor_player_id == "bob"

	for street in (NLHStreet.FLOP, NLHStreet.TURN, NLHStreet.RIVER):
		assert state.street is street
		first = state.actor_player_id
		assert first is not None
		second = "alice" if first == "bob" else "bob"
		state = act(state, first, PokerActionKind.CHECK).state
		state = act(state, second, PokerActionKind.CHECK).state

	assert state.street is NLHStreet.COMPLETE
	assert state.outcome is not None
	assert state.outcome.total_pot == 20
	assert sum(player.stack for player in state.players) == initial_total
	assert sum(award.amount for award in state.outcome.gross_awards) == 20
	assert state.deck.cards_remaining == 40
	assert len(state.deck.burned_cards) == 3


def test_short_all_in_is_called_and_runs_out_board() -> None:
	state = start_hand(
		(PlayerSeed("alice", 1, 15), PlayerSeed("bob", 4, 100)),
		button_seat=1,
		seed=73,
	).state

	state = act(state, "alice", PokerActionKind.ALL_IN).state
	assert state.current_bet == 15
	assert state.actor_player_id == "bob"
	state = act(state, "bob", PokerActionKind.CALL).state

	assert state.street is NLHStreet.COMPLETE
	assert state.outcome is not None
	assert state.outcome.total_pot == 30
	assert sum(player.stack for player in state.players) == 115
	assert len(state.community_cards) == 5


def test_short_all_in_raise_does_not_reopen_prior_actors() -> None:
	state = start_hand(
		(
			PlayerSeed("alice", 0, 100),
			PlayerSeed("bob", 1, 100),
			PlayerSeed("carol", 2, 100),
			PlayerSeed("dan", 3, 15),
			PlayerSeed("erin", 4, 100),
			PlayerSeed("frank", 5, 100),
		),
		button_seat=3,
		seed=74,
	).state
	assert state.actor_player_id == "alice"
	state = act(state, "alice", PokerActionKind.CALL).state
	state = act(state, "bob", PokerActionKind.FOLD).state
	state = act(state, "carol", PokerActionKind.FOLD).state
	state = act(state, "dan", PokerActionKind.ALL_IN).state
	state = act(state, "erin", PokerActionKind.CALL).state
	state = act(state, "frank", PokerActionKind.CALL).state

	assert state.actor_player_id == "alice"
	assert state.current_bet == 15
	assert PokerActionKind.CALL in kinds_for(state, "alice")
	assert PokerActionKind.RAISE not in kinds_for(state, "alice")
	with pytest.raises(ValueError, match="not been reopened"):
		act(state, "alice", PokerActionKind.RAISE, 25)
	with pytest.raises(ValueError, match="not been reopened"):
		act(state, "alice", PokerActionKind.ALL_IN)


def test_cumulative_short_all_ins_reopen_action_after_a_full_raise_increment() -> None:
	state = start_hand(
		(
			PlayerSeed("alice", 0, 100),
			PlayerSeed("bob", 1, 100),
			PlayerSeed("carol", 2, 100),
			PlayerSeed("dan", 3, 15),
			PlayerSeed("erin", 4, 20),
			PlayerSeed("frank", 5, 100),
		),
		button_seat=3,
		seed=75,
	).state
	state = act(state, "alice", PokerActionKind.CALL).state
	state = act(state, "bob", PokerActionKind.FOLD).state
	state = act(state, "carol", PokerActionKind.FOLD).state
	state = act(state, "dan", PokerActionKind.ALL_IN).state
	state = act(state, "erin", PokerActionKind.ALL_IN).state
	state = act(state, "frank", PokerActionKind.CALL).state

	assert state.actor_player_id == "alice"
	assert state.current_bet == 20
	assert state.current_bet - next(
		player.last_action_bet for player in state.players if player.player_id == "alice"
	) == state.minimum_raise
	assert PokerActionKind.RAISE in kinds_for(state, "alice")
	assert act(state, "alice", PokerActionKind.RAISE, 30).state.current_bet == 30