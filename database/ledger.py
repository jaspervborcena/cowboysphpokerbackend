from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from base_poker_game import calculate_hand_settlement_finances as _game_finance_calc
except Exception:  # pragma: no cover - fallback when imported in isolation
    _game_finance_calc = None


def calculate_hand_settlement_finances(
    total_pot: int,
    dealt_flop: bool = False,
    rake_cfg: dict[str, Any] | None = None,
    jackpot_cfg: dict[str, Any] | None = None,
    showdown_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _game_finance_calc is not None:
        return _game_finance_calc(
            total_pot=total_pot,
            dealt_flop=dealt_flop,
            rake_cfg=rake_cfg,
            jackpot_cfg=jackpot_cfg,
            showdown_data=showdown_data,
        )

    pot_total = max(0, int(total_pot))
    house_rake = 0
    jackpot_drop = 0

    if rake_cfg:
        percentage = rake_cfg.get("percentage", rake_cfg.get("rake_rate", rake_cfg.get("rate", 0)))
        cap_amount = rake_cfg.get("cap_amount", rake_cfg.get("rake_cap", 0))
        no_flop_no_drop = bool(rake_cfg.get("no_flop_no_drop", True))
        if percentage and (dealt_flop or not no_flop_no_drop):
            raw_rake = pot_total * float(percentage)
            house_rake = int(raw_rake) if not cap_amount else min(int(raw_rake), int(cap_amount))

    playable_pot = max(0, pot_total - house_rake)
    if jackpot_cfg and jackpot_cfg.get("enabled"):
        threshold = int(jackpot_cfg.get("min_pot_threshold", jackpot_cfg.get("min_pot", 0)))
        if playable_pot >= threshold:
            fee_amount = int(jackpot_cfg.get("fee_amount", jackpot_cfg.get("amount", 0)))
            jackpot_drop = max(0, min(fee_amount, playable_pot))

    net_winning_pot = max(0, playable_pot - jackpot_drop)
    return {
        "house_rake": house_rake,
        "jackpot_drop": jackpot_drop,
        "playable_pot": playable_pot,
        "net_winning_pot": net_winning_pot,
        "showdown_data": showdown_data,
    }


@dataclass
class LedgerEntry:
    transaction_id: str
    currency_type: str
    type: str
    amount: int
    from_user_id: str | None = None
    to_user_id: str | None = None
    performed_by: str | None = None
    note: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "currency_type": self.currency_type,
            "type": self.type,
            "amount": self.amount,
            "from_user_id": self.from_user_id,
            "to_user_id": self.to_user_id,
            "performed_by": self.performed_by,
            "note": self.note,
            "timestamp": self.timestamp,
        }


class WalletLedger:
    def __init__(self) -> None:
        self.members: dict[str, dict[str, Any]] = {}
        self.transactions: list[LedgerEntry] = []

    def set_member(self, user_id: str, *, chip_balance: int = 0, in_play_balance: int = 0, **extra: Any) -> dict[str, Any]:
        member = self.members.setdefault(
            user_id,
            {
                "user_id": user_id,
                "chip_balance": 0,
                "in_play_balance": 0,
                "credit_limit": 0,
                "role": "PLAYER",
            },
        )
        member["chip_balance"] = int(chip_balance)
        member["in_play_balance"] = int(in_play_balance)
        member.setdefault("role", extra.pop("role", "PLAYER"))
        member.setdefault("credit_limit", 0)
        member.update(extra)
        return member

    def get_member(self, user_id: str) -> dict[str, Any]:
        return self.members.setdefault(user_id, {"user_id": user_id, "chip_balance": 0, "in_play_balance": 0})

    def add_transaction(self, *, amount: int, type: str, currency_type: str, from_user_id: str | None = None, to_user_id: str | None = None, performed_by: str | None = None, note: str = "") -> LedgerEntry:
        entry = LedgerEntry(
            transaction_id=f"tx-{len(self.transactions) + 1}",
            currency_type=currency_type,
            type=type,
            amount=int(amount),
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            performed_by=performed_by,
            note=note,
        )
        self.transactions.append(entry)
        return entry

    def debit_user(self, user_id: str, amount: int, *, currency_type: str = "GLOBAL_PLAYMONEY", note: str = "") -> bool:
        member = self.get_member(user_id)
        amount = int(amount)
        if amount < 0 or member.get("chip_balance", 0) < amount:
            return False
        member["chip_balance"] = int(member.get("chip_balance", 0)) - amount
        self.add_transaction(
            amount=amount,
            type="DEBIT",
            currency_type=currency_type,
            from_user_id=user_id,
            performed_by=user_id,
            note=note,
        )
        return True

    def credit_user(self, user_id: str, amount: int, *, currency_type: str = "GLOBAL_PLAYMONEY", note: str = "") -> bool:
        member = self.get_member(user_id)
        amount = int(amount)
        if amount < 0:
            return False
        member["chip_balance"] = int(member.get("chip_balance", 0)) + amount
        self.add_transaction(
            amount=amount,
            type="CREDIT",
            currency_type=currency_type,
            to_user_id=user_id,
            performed_by=user_id,
            note=note,
        )
        return True


class GlobalWalletLedger(WalletLedger):
    def __init__(self) -> None:
        super().__init__()

    def get_balance(self, user_id: str) -> int:
        return int(self.get_member(user_id).get("chip_balance", 0))

    def debit(self, user_id: str, amount: int) -> bool:
        return self.debit_user(user_id, amount, currency_type="GLOBAL_PLAYMONEY", note="global_wallet_debit")

    def credit(self, user_id: str, amount: int) -> bool:
        return self.credit_user(user_id, amount, currency_type="GLOBAL_PLAYMONEY", note="global_wallet_credit")


class ClubWalletLedger(WalletLedger):
    def __init__(self) -> None:
        super().__init__()
        self.club_pool_state: dict[str, dict[str, int]] = {}
        self.clubs: dict[str, dict[str, Any]] = {}

    def set_member(self, club_id: str, user_id: str, *, chip_balance: int = 0, in_play_balance: int = 0, **extra: Any) -> dict[str, Any]:
        club_members = self.clubs.setdefault(club_id, {})
        member = club_members.setdefault(
            user_id,
            {
                "user_id": user_id,
                "club_id": club_id,
                "chip_balance": 0,
                "in_play_balance": 0,
                "credit_limit": 0,
                "role": "PLAYER",
            },
        )
        member["club_id"] = club_id
        member["chip_balance"] = int(chip_balance)
        member["in_play_balance"] = int(in_play_balance)
        member["role"] = extra.pop("role", member.get("role", "PLAYER"))
        member.setdefault("credit_limit", 0)
        member.update(extra)
        return member

    def get_member(self, club_id: str, user_id: str | None = None) -> dict[str, Any]:
        if user_id is None:
            return self.clubs.get(club_id, {})
        club_members = self.clubs.setdefault(club_id, {})
        return club_members.setdefault(user_id, {"user_id": user_id, "club_id": club_id, "chip_balance": 0, "in_play_balance": 0})

    def get_pool(self, club_id: str) -> dict[str, int]:
        return self.club_pool_state.setdefault(
            club_id,
            {
                "total_rake_collected": 0,
                "bad_beat_jackpot_pool": 0,
                "hand_of_the_day_pool": 0,
            },
        )

    def apply_pool_update(self, club_id: str, *, rake: int = 0, jackpot: int = 0, hand_of_the_day: int = 0) -> dict[str, int]:
        pool = self.get_pool(club_id)
        pool["total_rake_collected"] += int(rake)
        pool["bad_beat_jackpot_pool"] += int(jackpot)
        pool["hand_of_the_day_pool"] += int(hand_of_the_day)
        return dict(pool)

    def send_out(self, club_id: str, sender_user_id: str, recipient_user_id: str, amount: int, *, performed_by: str | None = None, note: str = "club_send_out") -> bool:
        amount = int(amount)
        if amount < 0:
            return False
        sender = self.get_member(club_id, sender_user_id)
        recipient = self.get_member(club_id, recipient_user_id)
        role = str(sender.get("role", "")).upper()
        if role not in {"OWNER", "MANAGER", "AGENT"}:
            return False
        if role == "AGENT" and recipient.get("agent_id") != sender_user_id:
            return False
        if sender.get("chip_balance", 0) < amount:
            return False
        sender["chip_balance"] = int(sender.get("chip_balance", 0)) - amount
        recipient["chip_balance"] = int(recipient.get("chip_balance", 0)) + amount
        self.transactions.append(
            LedgerEntry(
                transaction_id=f"tx-{len(self.transactions) + 1}",
                currency_type="CLUB_CHIPS",
                type="SEND_OUT",
                amount=amount,
                from_user_id=sender_user_id,
                to_user_id=recipient_user_id,
                performed_by=performed_by or sender_user_id,
                note=note,
            )
        )
        return True

    def claim_back(self, club_id: str, user_id: str, admin_user_id: str, amount: int, *, note: str = "club_claim_back") -> bool:
        amount = int(amount)
        if amount < 0:
            return False
        member = self.get_member(club_id, user_id)
        admin = self.get_member(club_id, admin_user_id)
        role = str(admin.get("role", "")).upper()
        if role not in {"OWNER", "MANAGER", "AGENT"}:
            return False
        if role == "AGENT" and member.get("agent_id") != admin_user_id:
            return False
        if int(member.get("in_play_balance", 0)) > 0:
            return False
        if int(member.get("chip_balance", 0)) < amount:
            return False
        member["chip_balance"] = int(member.get("chip_balance", 0)) - amount
        admin["chip_balance"] = int(admin.get("chip_balance", 0)) + amount
        self.transactions.append(
            LedgerEntry(
                transaction_id=f"tx-{len(self.transactions) + 1}",
                currency_type="CLUB_CHIPS",
                type="CLAIM_BACK",
                amount=amount,
                from_user_id=user_id,
                to_user_id=admin_user_id,
                performed_by=admin_user_id,
                note=note,
            )
        )
        return True


TwoTierWalletLedger = ClubWalletLedger
UnifiedFinancialLedger = ClubWalletLedger


__all__ = [
    "calculate_hand_settlement_finances",
    "LedgerEntry",
    "WalletLedger",
    "GlobalWalletLedger",
    "ClubWalletLedger",
    "TwoTierWalletLedger",
    "UnifiedFinancialLedger",
]
