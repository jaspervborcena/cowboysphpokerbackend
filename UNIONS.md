# Union System

## Purpose

A Union lets participating clubs share a lobby and poker tables while keeping member accounts and cashier balances owned by each player's home club. The backend validates the selected home-club membership for every seat and socket connection; a client-provided club ID is never sufficient on its own.

This document describes the current implementation and marks functionality that still needs a product decision. The detailed REST and WebSocket contract is in [API.md](API.md); Firestore field examples are in [database/README.md](database/README.md).

## Domain and authority

```text
Union
  Union Admins
  Participating Clubs
    Club Members and their club balances
  Shared Tables
    Seats retain each player's home club
  Daily Clearing Ledger
```

`UNION_ADMIN` is a union-scoped role stored at `unions/{union_id}/admins/{user_id}`. It is not a club role and does not grant `OWNER` or `MANAGER` rights inside any participating club. Active membership is required for both admin and player operations.

| Actor | Union capability |
| --- | --- |
| `UNION_ADMIN` | Create and start shared tables; delete empty shared tables. |
| Active club `PLAYER` in a participating club | Discover and join shared tables; play over the authenticated union socket. |
| Club `OWNER` or `MANAGER` | Manage local club tables only; no implied Union Admin rights. |

Union admin records and club-union links are provisioned manually in Firestore. The API does not yet create unions, approve clubs, issue invitations, or assign Union Admins.

## Player and table flow

1. The client loads the member's club identity with `GET /clubs/{club_id}/members/me`.
2. It loads local tables with `GET /clubs/{club_id}/tables` and active union memberships with `GET /clubs/{club_id}/unions`.
3. For a selected union, the client loads its current shared tables with `GET /clubs/{club_id}/unions/{union_id}/tables`.
4. A Union Admin creates a table with `POST /unions/{union_id}/tables/{table_id}`. The requested origin club must already be an active participant in that Union.
5. A player selects their home club and buys into a seat through `POST /unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats`. The server verifies the Firebase UID has an active `PLAYER` membership in that club and that the club belongs to the Union.
6. The UI connects to `ws://{host}/ws/union/{union_id}/{table_id}`. Its first message contains the ID token and selected `club_id`; the backend verifies both the club membership and the player's reserved seat.
7. The Union Admin starts a hand with `POST /unions/{union_id}/tables/{table_id}/hands`. All connected players receive complete snapshots through the same Union connection namespace. Hole cards remain player-filtered.
8. The table snapshot includes `is_union_table`, `union_id`, `origin_club_id`, and `table_name`. Each player entry includes `club_id` for the table seat label.
9. Completed hands use `GET /unions/{union_id}/hands/{hand_id}/replay?club_id={club_id}`. The response applies the same card-masking rules as local club replay; clients must never read raw Firestore audit documents.

The Angular lobby has separate Club Tables and Union Tables tabs, a union selector, home-club seat labels, and Union Admin-only create/delete controls. The API, not the UI, remains the authority for each permission.

## Firestore records

```text
unions/{union_id}
  admins/{user_id}
  clubs/{club_id}
  tables/{table_id}
    seats/{seat_index}
    hands/{hand_id}
  settlements/{YYYY-MM-DD}
    clubs/{club_id}
    hands/{hand_id}
```

Required manually provisioned membership documents:

```json
{"role":"UNION_ADMIN","status":"active"}
```

at `unions/{union_id}/admins/{user_id}`, and:

```json
{
  "union_id": "union-1",
  "club_id": "club-a",
  "union_name": "Pinoy Poker Alliance",
  "status": "active"
}
```

at `unions/{union_id}/clubs/{club_id}`.

Table configuration is persisted under `unions/{union_id}/tables/{table_id}`. Deleting an empty table marks its metadata `closed`; it does not erase its audit trail. Buy-in transactions debit the selected home club's `chip_balance`, increase its `in_play_balance`, and reserve a Union seat that includes `club_id`. This makes the club identity durable for settlement and audit.

## Inter-club clearing

At hand settlement, the same Firestore transaction:

- applies each player's `net_delta` to that player's home-club `in_play_balance`;
- updates Union seat stacks;
- stores the restricted hand audit;
- aggregates player deltas by `club_id` and updates that date's club clearing position;
- stores a per-hand settlement record so retries do not apply balances or clearing twice.

For a club, let $N$ be the sum of its players' hand `net_delta` values. The recorded position is:

- debt = $\max(-N, 0)$;
- credit = $\max(N, 0)$.

Example: if Club A's player loses 500 chips to Club B's player, A's debt increases by 500 and B's credit increases by 500. These records measure the clearing position; they do not transfer money between clubs or cash out player balances.

The game currently charges no rake, so `rake_amount` is zero. The Union software fee and traffic-based rake split are not implemented; do not book or promise a rake share until the rate, eligible pot, rounding, and allocation policy are explicitly decided.

## Current limits

- Union membership and Union Admin provisioning are manual; no formation, invitation, approval, or role-management API exists.
- Union table listing reflects tables running in the current process. Firestore stores table configuration and closed status, but the game engine does not recover active tables after restart.
- A seated table cannot be deleted. There is no leave-seat or cash-out route, so seats remain until those flows are designed and implemented.
- The shared WebSocket namespace works within one process. Cross-instance ownership, locking, and durable single-writer behavior are not implemented.
- Clearing positions are recorded by hand/day; club-to-club payout, reconciliation, cashier transfers, and automated daily close are not implemented.
- Rake collection and rake allocation are not implemented.

Before production use, provision Firestore security rules so clients cannot read Union audit, settlement, or private seat data directly. Configure exact frontend CORS origins with `CORS_ALLOWED_ORIGINS`. Validate multi-instance behavior and reconcile clearing ledgers in the Firestore emulator before enabling real balances.
