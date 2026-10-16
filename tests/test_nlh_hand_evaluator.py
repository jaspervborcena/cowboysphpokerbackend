import pytest

from game_engine import Card, HandCategory, HandRank, evaluate_hand


@pytest.mark.parametrize(
    ("cards", "expected"),
    [
        (["As", "Ks", "Qs", "Js", "Ts"], HandRank(HandCategory.STRAIGHT_FLUSH, (14,))),
        (["9c", "8c", "7c", "6c", "5c"], HandRank(HandCategory.STRAIGHT_FLUSH, (9,))),
        (["Ah", "Ad", "Ac", "As", "Kd"], HandRank(HandCategory.FOUR_OF_A_KIND, (14, 13))),
        (["Kh", "Kd", "Kc", "2s", "2d"], HandRank(HandCategory.FULL_HOUSE, (13, 2))),
        (["Ah", "Jh", "8h", "5h", "2h"], HandRank(HandCategory.FLUSH, (14, 11, 8, 5, 2))),
        (["Ah", "2d", "3c", "4s", "5h"], HandRank(HandCategory.STRAIGHT, (5,))),
        (["Qh", "Qd", "Qc", "As", "9s"], HandRank(HandCategory.THREE_OF_A_KIND, (12, 14, 9))),
        (["Jh", "Jd", "4c", "4s", "As"], HandRank(HandCategory.TWO_PAIR, (11, 4, 14))),
        (["Th", "Td", "As", "Kc", "4d"], HandRank(HandCategory.ONE_PAIR, (10, 14, 13, 4))),
        (["Ah", "Jd", "8c", "5s", "2h"], HandRank(HandCategory.HIGH_CARD, (14, 11, 8, 5, 2))),
    ],
)
def test_five_card_hand_rank_golden_cases(
    cards: list[str], expected: HandRank
) -> None:
    assert evaluate_hand(cards) == expected


def test_evaluates_best_five_from_seven_cards() -> None:
    cards = ["As", "Ad", "Ac", "Ah", "Ks", "Kd", "2c"]

    assert evaluate_hand(cards) == HandRank(HandCategory.FOUR_OF_A_KIND, (14, 13))


def test_rank_comparison_uses_category_then_kickers() -> None:
    pair_of_kings_with_ace = evaluate_hand(["Kh", "Kd", "As", "9c", "4d"])
    pair_of_kings_with_queen = evaluate_hand(["Ks", "Kc", "Qh", "9d", "4s"])
    six_high_straight = evaluate_hand(["2h", "3d", "4c", "5s", "6h"])
    wheel_straight = evaluate_hand(["Ah", "2d", "3c", "4s", "5h"])

    assert pair_of_kings_with_ace > pair_of_kings_with_queen
    assert six_high_straight > wheel_straight


def test_card_parser_normalizes_ten_and_face_card_codes() -> None:
    assert Card.from_code("10S") == Card.from_code("Ts")
    assert Card.from_code("ah").code == "Ah"


@pytest.mark.parametrize(
    "cards",
    [
        ["As", "As", "Kd", "Qc", "Jh"],
        ["As", "Kd", "Qc", "Jh"],
        ["As", "Kd", "Qc", "Jh", "9s", "8c", "7d", "6h"],
        ["1s", "Kd", "Qc", "Jh", "9s"],
    ],
)
def test_rejects_invalid_hands(cards: list[str]) -> None:
    with pytest.raises(ValueError):
        evaluate_hand(cards)