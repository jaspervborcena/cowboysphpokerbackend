from app.api.hand_history import filter_replay_for_user


def test_replay_masks_all_opponents_when_hand_did_not_reach_showdown() -> None:
    hand_data = {
        "hand_id": "hand-1",
        "is_showdown": False,
        "frames": [
            {
                "frame_index": 0,
                "community_cards": [],
                "players": [
                    {
                        "user_id": "player-a",
                        "seat": 0,
                        "hole_cards": ["As", "Ad"],
                        "mucked": False,
                        "in_hand": True,
                    },
                    {
                        "user_id": "player-b",
                        "seat": 1,
                        "hole_cards": ["Kd", "Qd"],
                        "mucked": False,
                        "in_hand": False,
                    },
                ],
            }
        ],
        "operations": [{"type": "PrivateAuditOnly"}],
        "deck_cards": ["secret-card"],
    }

    replay = filter_replay_for_user(hand_data, "player-a")

    assert replay["frames"][0]["players"][0]["hole_cards"] == ["As", "Ad"]
    assert replay["frames"][0]["players"][1]["hole_cards"] == ["??", "??"]
    assert "operations" not in replay
    assert "deck_cards" not in replay
    assert hand_data["frames"][0]["players"][1]["hole_cards"] == ["Kd", "Qd"]


def test_showdown_reveals_only_unmucked_players_who_reached_showdown() -> None:
    hand_data = {
        "hand_id": "hand-2",
        "is_showdown": True,
        "frames": [
            {
                "frame_index": 1,
                "players": [
                    {
                        "user_id": "player-a",
                        "hole_cards": ["As", "Ad"],
                        "mucked": False,
                        "in_hand": True,
                    },
                    {
                        "user_id": "player-b",
                        "hole_cards": ["Kd", "Qd"],
                        "mucked": False,
                        "in_hand": True,
                    },
                    {
                        "user_id": "player-c",
                        "hole_cards": ["2c", "3c"],
                        "mucked": True,
                        "in_hand": True,
                    },
                    {
                        "user_id": "player-d",
                        "hole_cards": ["4c", "5c"],
                        "mucked": False,
                        "in_hand": False,
                    },
                ],
            }
        ],
    }

    replay = filter_replay_for_user(hand_data, "spectator")
    players = replay["frames"][0]["players"]

    assert players[0]["hole_cards"] == ["As", "Ad"]
    assert players[1]["hole_cards"] == ["Kd", "Qd"]
    assert players[2]["hole_cards"] == ["??", "??"]
    assert players[3]["hole_cards"] == ["??", "??"]