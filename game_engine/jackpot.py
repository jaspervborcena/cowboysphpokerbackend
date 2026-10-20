from __future__ import annotations

from itertools import combinations
from typing import Any

from game_engine.hand_evaluator import Card, HandCategory, HandRank, evaluate_hand


BBJ_RULE_VERSION = "nlh-bbj-aces-full-v1"
BBJ_GAME_ID = "no_limit_texas_holdem"
BBJ_MIN_PLAYERS = 4
BBJ_MIN_POT_BIG_BLINDS = 20
BBJ_RESEED_RESERVE_FLOOR = 0
BBJ_POOL_ACCESS_POINTS = (
    (2, 100),
    (50, 500),
    (100, 1000),
    (200, 1500),
)
BBJ_PAYOUT_PERCENTAGES = (("losers", 50), ("winners", 25), ("table", 25))
_BBJ_MIN_LOSER_RANK = HandRank(HandCategory.FULL_HOUSE, (14, 2))
_BBJ_MIN_WINNER_CATEGORY = HandCategory.FOUR_OF_A_KIND
_BBJ_MAX_POOL_ACCESS_BASIS_POINTS = 1500
HAND_OF_DAY_RULE_VERSION = "holdem-hotd-quads-or-better-v1"


def evaluate_bad_beat_qualification(
    players: list[dict[str, Any]],
    board: list[str],
    pot_total: int,
    big_blind: int,
    is_showdown: bool,
    enabled: bool,
) -> dict[str, Any]:
    """Evaluate the versioned Hold'em BBJ rule and return private audit evidence."""
    if (
        isinstance(pot_total, bool)
        or not isinstance(pot_total, int)
        or pot_total < 0
    ):
        raise ValueError("Pot total must be a non-negative integer.")
    if (
        isinstance(big_blind, bool)
        or not isinstance(big_blind, int)
        or big_blind <= 0
    ):
        raise ValueError("Big blind must be a positive integer.")
    result: dict[str, Any] = {
        "game_id": BBJ_GAME_ID,
        "rule_version": BBJ_RULE_VERSION,
        "eligible": False,
        "reason_code": "",
        "dealt_in_player_count": len(players),
        "pot_total": pot_total,
        "big_blind": big_blind,
        "minimum_player_count": BBJ_MIN_PLAYERS,
        "minimum_pot": BBJ_MIN_POT_BIG_BLINDS * big_blind,
        "qualifying_losers": [],
        "qualifying_winners": [],
        "evaluated_hands": [],
    }
    if not enabled:
        result["reason_code"] = "PROMOTION_DISABLED"
        return result
    if not is_showdown:
        result["reason_code"] = "NOT_SHOWDOWN"
        return result
    if len(players) < BBJ_MIN_PLAYERS:
        result["reason_code"] = "INSUFFICIENT_DEALT_IN_PLAYERS"
        return result
    if pot_total < BBJ_MIN_POT_BIG_BLINDS * big_blind:
        result["reason_code"] = "POT_BELOW_MINIMUM"
        result["minimum_pot"] = BBJ_MIN_POT_BIG_BLINDS * big_blind
        return result
    if len(board) != 5:
        result["reason_code"] = "INCOMPLETE_BOARD"
        return result

    ranked_players: list[tuple[HandRank, dict[str, Any]]] = []
    for player in players:
        if player.get("folded", False):
            continue
        hole_cards = player.get("hole_cards")
        if not isinstance(hole_cards, list) or len(hole_cards) != 2:
            raise ValueError("BBJ evaluation requires two hole cards per player.")
        rank, best_five = _best_five((*hole_cards, *board))
        best_codes = {card.code for card in best_five}
        evidence = {
            "user_id": str(player["user_id"]),
            "seat": int(player["seat"]),
            "rank_category": rank.category.name,
            "rank_tiebreakers": list(rank.tiebreakers),
            "hole_cards": [card for card in hole_cards if card in best_codes],
            "board_cards": [card for card in board if card in best_codes],
        }
        ranked_players.append((rank, evidence))
        result["evaluated_hands"].append(evidence)

    if len(ranked_players) < 2:
        result["reason_code"] = "INSUFFICIENT_SHOWDOWN_PLAYERS"
        return result

    winning_rank = max(rank for rank, _ in ranked_players)
    if winning_rank.category < _BBJ_MIN_WINNER_CATEGORY:
        result["reason_code"] = "WINNING_HAND_BELOW_MINIMUM"
        return result

    winners = [
        evidence
        for rank, evidence in ranked_players
        if rank == winning_rank
    ]
    losers = [
        evidence
        for rank, evidence in ranked_players
        if _BBJ_MIN_LOSER_RANK <= rank < winning_rank
    ]
    if not losers:
        result["reason_code"] = "NO_QUALIFYING_LOSING_HAND"
        return result

    result["eligible"] = True
    result["reason_code"] = "QUALIFIED"
    result["qualifying_losers"] = losers
    result["qualifying_winners"] = winners
    return result


def evaluate_hand_of_day(
    players: list[dict[str, Any]],
    board: list[str],
    game_id: str,
    is_showdown: bool,
) -> dict[str, Any]:
    """Return private evidence for the strongest quads-or-better hand in an NLH showdown."""
    result: dict[str, Any] = {
        "game_id": game_id,
        "rule_version": HAND_OF_DAY_RULE_VERSION,
        "eligible": False,
        "reason_code": "",
        "leaders": [],
    }
    if game_id != BBJ_GAME_ID:
        result["reason_code"] = "UNSUPPORTED_GAME"
        return result
    if not is_showdown:
        result["reason_code"] = "NOT_SHOWDOWN"
        return result
    if len(board) != 5:
        result["reason_code"] = "INCOMPLETE_BOARD"
        return result

    ranked: list[tuple[HandRank, dict[str, Any]]] = []
    for player in players:
        if player.get("folded", False):
            continue
        hole_cards = player.get("hole_cards")
        if not isinstance(hole_cards, list) or len(hole_cards) != 2:
            raise ValueError("Hand-of-the-Day evaluation requires two hole cards per player.")
        rank, best_five = _best_five((*hole_cards, *board))
        if rank.category < HandCategory.FOUR_OF_A_KIND:
            continue
        best_codes = {card.code for card in best_five}
        ranked.append(
            (
                rank,
                {
                    "user_id": str(player["user_id"]),
                    "username": str(player.get("username") or player["user_id"]),
                    "seat": int(player["seat"]),
                    "rank_category": rank.category.name,
                    "rank_tiebreakers": list(rank.tiebreakers),
                    "rank_value": [int(rank.category), *rank.tiebreakers],
                    "hole_cards": [card for card in hole_cards if card in best_codes],
                    "board_cards": [card for card in board if card in best_codes],
                },
            )
        )

    if not ranked:
        result["reason_code"] = "NO_QUALIFYING_HAND"
        return result
    best_rank = max(rank for rank, _ in ranked)
    result["eligible"] = True
    result["reason_code"] = "QUALIFIED"
    result["leaders"] = [
        evidence for rank, evidence in ranked if rank == best_rank
    ]
    result["rank_value"] = [int(best_rank.category), *best_rank.tiebreakers]
    return result


def calculate_bad_beat_award(
    pool_balance: int,
    big_blind: int,
    players: list[dict[str, Any]],
    qualification: dict[str, Any],
) -> dict[str, Any]:
    """Calculate capped BBJ shares using exact integer arithmetic."""
    if isinstance(pool_balance, bool) or not isinstance(pool_balance, int) or pool_balance < 0:
        raise ValueError("BBJ pool balance must be a non-negative integer.")
    if isinstance(big_blind, bool) or not isinstance(big_blind, int) or big_blind <= 0:
        raise ValueError("Big blind must be a positive integer.")
    if not qualification.get("eligible"):
        raise ValueError("Cannot calculate a BBJ award for an unqualified hand.")

    access_numerator, access_denominator = _pool_access_rate(big_blind)
    effective_numerator = min(
        access_numerator,
        _BBJ_MAX_POOL_ACCESS_BASIS_POINTS * access_denominator,
    )
    effective_denominator = access_denominator * 10_000
    available = max(pool_balance - BBJ_RESEED_RESERVE_FLOOR, 0)
    candidate_award = (
        pool_balance * effective_numerator // effective_denominator
    )
    award_amount = min(candidate_award, available)
    share_amounts = _allocate_percentages(
        award_amount, BBJ_PAYOUT_PERCENTAGES
    )

    player_by_id = {str(player["user_id"]): player for player in players}
    losers = _seat_ordered_recipients(
        qualification["qualifying_losers"], player_by_id
    )
    winners = _seat_ordered_recipients(
        qualification["qualifying_winners"], player_by_id
    )
    qualifier_ids = {
        str(recipient["user_id"]) for recipient in (*losers, *winners)
    }
    table_recipients = sorted(
        (
            player
            for player in players
            if player.get("rake_eligible", False)
            and str(player["user_id"]) not in qualifier_ids
        ),
        key=lambda player: (int(player["seat"]), str(player["user_id"])),
    )
    loser_credits = _split_equally(share_amounts["losers"], losers)
    winner_credits = _split_equally(share_amounts["winners"], winners)
    table_credits = _split_equally(share_amounts["table"], table_recipients)
    returned_to_pool = share_amounts["table"] - sum(table_credits.values())
    paid_amount = sum(loser_credits.values()) + sum(winner_credits.values()) + sum(
        table_credits.values()
    )

    return {
        "rule_version": qualification["rule_version"],
        "game_id": BBJ_GAME_ID,
        "big_blind": big_blind,
        "pool_balance_before_award": pool_balance,
        "configured_pool_access_percent": (
            access_numerator / access_denominator / 100
        ),
        "effective_pool_access_percent": (
            effective_numerator / access_denominator / 100
        ),
        "reseed_reserve_floor": BBJ_RESEED_RESERVE_FLOOR,
        "award_amount": award_amount,
        "paid_amount": paid_amount,
        "returned_to_pool": returned_to_pool,
        "payout_split_percentages": dict(BBJ_PAYOUT_PERCENTAGES),
        "loser_share": share_amounts["losers"],
        "winner_share": share_amounts["winners"],
        "table_share": share_amounts["table"],
        "loser_credits": loser_credits,
        "winner_credits": winner_credits,
        "table_credits": table_credits,
        "pool_balance_after_award": pool_balance - paid_amount,
    }


def _best_five(cards: tuple[str, ...]) -> tuple[HandRank, tuple[Card, ...]]:
    parsed = tuple(Card.from_code(card) for card in cards)
    best_rank: HandRank | None = None
    best_cards: tuple[Card, ...] | None = None
    for candidate in combinations(parsed, 5):
        rank = evaluate_hand(candidate)
        candidate_key = tuple(sorted(card.code for card in candidate))
        best_key = (
            tuple(sorted(card.code for card in best_cards))
            if best_cards is not None
            else ()
        )
        if best_rank is None or rank > best_rank or (
            rank == best_rank and candidate_key < best_key
        ):
            best_rank = rank
            best_cards = candidate
    if best_rank is None or best_cards is None:
        raise ValueError("A Hold'em hand must contain at least five cards.")
    return best_rank, best_cards


def _pool_access_rate(big_blind: int) -> tuple[int, int]:
    points = BBJ_POOL_ACCESS_POINTS
    if big_blind <= points[0][0]:
        return points[0][1], 1
    if big_blind >= points[-1][0]:
        return points[-1][1], 1
    for (lower_blind, lower_rate), (upper_blind, upper_rate) in zip(
        points, points[1:]
    ):
        if lower_blind <= big_blind <= upper_blind:
            span = upper_blind - lower_blind
            numerator = lower_rate * span + (big_blind - lower_blind) * (
                upper_rate - lower_rate
            )
            return numerator, span
    raise RuntimeError("No BBJ access-rate interval found.")


def _allocate_percentages(
    amount: int, percentages: tuple[tuple[str, int], ...]
) -> dict[str, int]:
    shares = {
        name: amount * percentage // 100 for name, percentage in percentages
    }
    remainders = {
        name: amount * percentage % 100 for name, percentage in percentages
    }
    remaining = amount - sum(shares.values())
    order = {name: index for index, (name, _) in enumerate(percentages)}
    for name in sorted(
        shares, key=lambda item: (-remainders[item], order[item])
    )[:remaining]:
        shares[name] += 1
    return shares


def _seat_ordered_recipients(
    evidence: list[dict[str, Any]],
    player_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    recipients = [
        player_by_id[str(entry["user_id"])]
        for entry in evidence
        if str(entry["user_id"]) in player_by_id
    ]
    return sorted(
        recipients,
        key=lambda player: (int(player["seat"]), str(player["user_id"])),
    )


def _split_equally(amount: int, recipients: list[dict[str, Any]]) -> dict[str, int]:
    credits = {str(player["user_id"]): 0 for player in recipients}
    if amount == 0 or not recipients:
        return credits
    quotient, remainder = divmod(amount, len(recipients))
    for index, player in enumerate(recipients):
        credits[str(player["user_id"])] = quotient + int(index < remainder)
    return credits
