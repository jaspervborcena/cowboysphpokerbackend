from __future__ import annotations

from importlib.metadata import version
from typing import Any


POKERKIT_VERSION = version("pokerkit")
SUPPORTED_GAME_ID = "no_limit_texas_holdem"
SUPPORTED_GAME_IDS = frozenset({SUPPORTED_GAME_ID})
_UNVERIFIED_REASON = (
    "PokerKit provides this game factory, but gameplay, settlement, and replay "
    "integration have not been verified by this backend."
)


def is_game_create_supported(game_id: str) -> bool:
    return game_id in SUPPORTED_GAME_IDS


_GAME_DEFINITIONS = (
    ("fixed_limit_texas_holdem", "Fixed Limit Texas Hold'em", "Hold'em", "FixedLimitTexasHoldem"),
    ("no_limit_texas_holdem", "No Limit Texas Hold'em", "Hold'em", "NoLimitTexasHoldem"),
    ("no_limit_short_deck_holdem", "No Limit Short Deck Hold'em", "Hold'em", "NoLimitShortDeckHoldem"),
    ("no_limit_royal_holdem", "No Limit Royal Hold'em", "Hold'em", "NoLimitRoyalHoldem"),
    ("pot_limit_omaha_holdem", "Pot Limit Omaha Hold'em", "Omaha", "PotLimitOmahaHoldem"),
    (
        "fixed_limit_omaha_high_low_split_eight_or_better",
        "Fixed Limit Omaha Hi-Lo Eight or Better",
        "Omaha",
        "FixedLimitOmahaHoldemHighLowSplitEightOrBetter",
    ),
    ("fixed_limit_seven_card_stud", "Fixed Limit Seven Card Stud", "Stud", "FixedLimitSevenCardStud"),
    (
        "fixed_limit_seven_card_stud_high_low_split_eight_or_better",
        "Fixed Limit Seven Card Stud Hi-Lo Eight or Better",
        "Stud",
        "FixedLimitSevenCardStudHighLowSplitEightOrBetter",
    ),
    ("fixed_limit_razz", "Fixed Limit Razz", "Stud", "FixedLimitRazz"),
    (
        "no_limit_deuce_to_seven_lowball_single_draw",
        "No Limit Deuce-to-Seven Single Draw",
        "Draw",
        "NoLimitDeuceToSevenLowballSingleDraw",
    ),
    (
        "fixed_limit_deuce_to_seven_lowball_triple_draw",
        "Fixed Limit Deuce-to-Seven Triple Draw",
        "Draw",
        "FixedLimitDeuceToSevenLowballTripleDraw",
    ),
    ("fixed_limit_badugi", "Fixed Limit Badugi", "Draw", "FixedLimitBadugi"),
    ("kuhn_poker", "Kuhn Poker", "Other", "KuhnPoker"),
    ("leduc_holdem", "Leduc Hold'em", "Other", "LeducHoldem"),
    ("numeral_211_holdem", "Numeral 21.1 Hold'em", "Other", "Numeral211Holdem"),
    ("rhode_island_holdem", "Rhode Island Hold'em", "Other", "RhodeIslandHoldem"),
    ("royal_rhode_island_holdem", "Royal Rhode Island Hold'em", "Other", "RoyalRhodeIslandHoldem"),
)


def get_game_catalog() -> list[dict[str, Any]]:
    catalog = []
    for game_id, display_name, category, factory_name in _GAME_DEFINITIONS:
        supported = is_game_create_supported(game_id)
        catalog.append(
            {
                "game_id": game_id,
                "display_name": display_name,
                "category": category,
                "pokerkit_factory": factory_name,
                "pokerkit_available": True,
                "backend_status": "supported" if supported else "mock",
                "create_supported": supported,
                "mock_reason": None if supported else _UNVERIFIED_REASON,
            }
        )
    return catalog