from __future__ import annotations

from random import Random

from hypothesis import given, settings, strategies as st

from game_engine import (
    Deck,
    HandCategory,
    HandRank,
    NLHStreet,
    PlayerSeed,
    PokerAction,
    PokerTableConfig,
    apply_nlh_action,
    build_side_pots,
    legal_nlh_actions,
    resolve_pot_awards,
    start_nlh_hand,
)


@st.composite
def _hand_cases(draw):
    player_count = draw(st.integers(min_value=2, max_value=6))
    stacks = draw(
        st.lists(
            st.integers(min_value=10, max_value=250),
            min_size=player_count,
            max_size=player_count,
        )
    )
    button_seat = draw(st.integers(min_value=0, max_value=player_count - 1))
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    return stacks, button_seat, seed


@st.composite
def _pot_cases(draw):
    player_count = draw(st.integers(min_value=2, max_value=6))
    contributions = draw(
        st.lists(
            st.integers(min_value=0, max_value=500),
            min_size=player_count,
            max_size=player_count,
        )
    )
    folded_flags = draw(
        st.lists(st.booleans(), min_size=player_count, max_size=player_count)
    )
    highest_contributor = max(
        range(player_count), key=contributions.__getitem__
    )
    folded_ids = {
        f"player-{index}"
        for index, folded in enumerate(folded_flags)
        if folded and index != highest_contributor
    }
    return contributions, folded_ids


@given(case=_hand_cases())
@settings(max_examples=60, deadline=None)
def test_random_legal_hands_conserve_chips_and_use_unique_cards(case) -> None:
    stacks, button_seat, seed = case
    players = tuple(
        PlayerSeed(f"player-{index}", index, stack)
        for index, stack in enumerate(stacks)
    )
    deck = Deck.for_testing(seed)
    state = start_nlh_hand(
        "property-table",
        f"property-hand-{seed}",
        PokerTableConfig("no_limit_texas_holdem", 5, 10, 6),
        players,
        button_seat,
        deck,
    ).state
    chooser = Random(seed)
    starting_chips = sum(stacks)

    for _ in range(300):
        dealt_codes = [
            card.code
            for player in state.players
            for card in player.hole_cards
        ]
        dealt_codes.extend(card.code for card in state.community_cards)
        dealt_codes.extend(card.code for card in deck.burned_cards)
        assert len(dealt_codes) == len(set(dealt_codes))

        if state.street is NLHStreet.COMPLETE:
            break

        assert state.actor_player_id is not None
        actions = legal_nlh_actions(state, state.actor_player_id)
        assert actions
        action = chooser.choice(actions)
        state = apply_nlh_action(state, action).state

        assert all(player.stack >= 0 for player in state.players)
        if state.street is not NLHStreet.COMPLETE:
            assert sum(
                player.stack + player.total_contribution for player in state.players
            ) == starting_chips
    else:
        raise AssertionError("Random legal-action simulation did not complete.")

    assert state.outcome is not None
    assert sum(player.stack for player in state.players) == starting_chips
    contributions_total = sum(player.total_contribution for player in state.players)
    awards_total = sum(award.amount for award in state.outcome.gross_awards)
    returns_total = sum(award.amount for award in state.outcome.uncalled_returns)
    assert awards_total + returns_total == contributions_total
    assert state.outcome.total_pot == awards_total


@given(case=_pot_cases())
@settings(max_examples=100, deadline=None)
def test_random_side_pots_and_tied_awards_conserve_contributions(case) -> None:
    contributions, folded_ids = case
    player_ids = [f"player-{index}" for index in range(len(contributions))]
    contribution_map = dict(zip(player_ids, contributions, strict=True))
    result = build_side_pots(contribution_map, folded_ids)

    pots_total = sum(pot.amount for pot in result.pots)
    returns_total = sum(result.uncalled_returns.values())
    assert pots_total + returns_total == sum(contributions)
    assert all(
        not folded_ids.intersection(pot.eligible_player_ids)
        for pot in result.pots
    )

    eligible_ids = {
        player_id
        for pot in result.pots
        for player_id in pot.eligible_player_ids
    }
    tied_rank = HandRank(HandCategory.HIGH_CARD, (14, 13, 9, 4, 2))
    awards = resolve_pot_awards(
        result.pots,
        {player_id: tied_rank for player_id in eligible_ids},
        player_ids,
    )
    assert sum(awards.values()) == pots_total
    assert not folded_ids.intersection(awards)