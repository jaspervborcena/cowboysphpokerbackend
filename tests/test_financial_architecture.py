from database.ledger import ClubWalletLedger, calculate_hand_settlement_finances


def test_calculate_hand_settlement_finances_matches_two_tier_economy_rules() -> None:
    finances = calculate_hand_settlement_finances(
        total_pot=2000,
        dealt_flop=True,
        rake_cfg={"percentage": 0.075, "cap_amount": 150, "no_flop_no_drop": True},
        jackpot_cfg={"enabled": True, "fee_amount": 50, "min_pot_threshold": 1000},
    )

    assert finances["house_rake"] == 150
    assert finances["jackpot_drop"] == 50
    assert finances["playable_pot"] == 1850
    assert finances["net_winning_pot"] == 1800


def test_club_wallet_send_out_and_claim_back_are_atomic_and_safe() -> None:
    ledger = ClubWalletLedger()
    ledger.set_member("club-1", "agent-1", chip_balance=1000, in_play_balance=0, role="AGENT")
    ledger.set_member("club-1", "manager-1", chip_balance=100, in_play_balance=0, role="MANAGER")
    ledger.set_member("club-1", "owner-1", chip_balance=100, in_play_balance=0, role="OWNER")
    ledger.set_member("club-1", "player-a", chip_balance=300, in_play_balance=0, role="PLAYER", agent_id="agent-1")
    ledger.set_member("club-1", "player-b", chip_balance=100, in_play_balance=0, role="PLAYER", agent_id="agent-2")
    ledger.set_member("club-1", "player-c", chip_balance=0, in_play_balance=0, role="PLAYER")

    assert ledger.send_out("club-1", "agent-1", "player-a", 250)
    assert ledger.get_member("club-1", "agent-1")["chip_balance"] == 750
    assert ledger.get_member("club-1", "player-a")["chip_balance"] == 550
    assert ledger.send_out("club-1", "agent-1", "player-b", 50) is False

    assert ledger.claim_back("club-1", "player-a", "agent-1", 100)
    assert ledger.get_member("club-1", "player-a")["chip_balance"] == 450
    assert ledger.get_member("club-1", "agent-1")["chip_balance"] == 850
    assert ledger.send_out("club-1", "manager-1", "player-c", 20)
    assert ledger.send_out("club-1", "owner-1", "player-c", 20)
    assert ledger.claim_back("club-1", "player-c", "manager-1", 10)
    assert ledger.claim_back("club-1", "player-c", "owner-1", 10)

    ledger.set_member("club-1", "player-d", chip_balance=1000, in_play_balance=150, role="PLAYER", agent_id="agent-1")
    assert ledger.claim_back("club-1", "player-d", "agent-1", 100) is False
    assert ledger.get_member("club-1", "player-d")["chip_balance"] == 1000

    assert ledger.send_out("club-1", "player-a", "player-b", 1) is False
