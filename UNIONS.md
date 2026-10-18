# Union System

## Purpose

A Union lets participating clubs share a lobby and poker tables while keeping member accounts and cashier balances owned by each player's home club. The backend validates the selected home-club membership for every seat and socket connection; a client-provided club ID is never sufficient on its own.

This document describes the current implementation and marks functionality that still needs a product decision. The detailed REST and WebSocket contract is in [API.md](API.md); Firestore field examples are in [database/README.md](database/README.md).

Current club REST route templates use `{club_id}` as the parameter name, but callers must pass the club UUID (`club_uuid`) returned by the membership API. Union seat paths use that same UUID. The union WebSocket's first-message field is also named `club_id` and currently expects the home club UUID, not the 8-digit business ID.

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

`UNION_ADMIN` is a union-scoped leadership role stored at `unions/{union_id}/admins/{user_id}`. It is not a club role and does not grant `OWNER` or `MANAGER` rights inside any participating club. The creator is made the initial union admin and representative for the founding club. Other club owners/managers first request representative approval; an approved representative can then submit their club for a separate union-admin approval.

| Actor | Union capability |
| --- | --- |
| `UNION_ADMIN` | Approve representative requests and club membership requests. |
| Active participating club `OWNER` or `MANAGER` | Create, configure, start, and delete tables owned by their club; link creation to an active union. |
| Active club `PLAYER` in a participating club | Discover and join shared tables; play over the authenticated union socket. |

Union creation assigns a unique one-letter `union_code` and a `theme_id`. The code resolves through `union_codes/{union_code}` to the stable `union_id`. Representative approval does not itself add a club to the union; a second explicit club request and approval is required.

## Player and table flow

1. The client loads the member's club identity with `GET /clubs/{club_id}/members/me`.
2. A club owner/manager creates a union with `POST /clubs/{club_id}/unions`, or looks one up by letter with `GET /unions/code/{union_code}`.
3. A prospective club representative submits `POST /clubs/{club_id}/unions/{union_id}/representative-requests`; a union admin reviews requests and approves with `POST /unions/{union_id}/representatives/{user_id}/approve`.
4. The approved representative requests club membership with `POST /clubs/{club_id}/unions/{union_id}/requests`; a union admin reviews and approves with `POST /unions/{union_id}/clubs/{club_id}/approve`.
5. The client loads local tables with `GET /clubs/{club_id}/tables` and active union memberships with `GET /clubs/{club_id}/unions`. For a selected union, it loads shared tables with `GET /clubs/{club_id}/unions/{union_id}/tables`.
6. An active member club's `OWNER`/`MANAGER` creates a table with `POST /clubs/{club_id}/tables/{table_id}` and includes `union_id`. The server stores it under the union scope, tags the origin club, and applies the union theme/code. A table without `union_id` remains local to its club.
7. A player selects their home club and buys into a seat through `POST /unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats`. The server verifies the Firebase UID has an active `PLAYER` membership in that club and that the club belongs to the union.
8. The UI connects to `ws://{host}/ws/union/{union_id}/{table_id}`. Its first message contains the ID token and selected home-club UUID in the `club_id` field; the backend verifies both the club membership and the player's reserved seat.
9. The origin club's `OWNER`/`MANAGER` starts a hand with `POST /unions/{union_id}/tables/{table_id}/hands`. All connected players receive complete snapshots through the same union connection namespace. Hole cards remain player-filtered.
10. The table snapshot includes `is_union_table`, `union_id`, `union_code`, `theme_id`, `origin_club_id`, and `table_name`. Each player entry includes `club_id` for the table seat label.
11. Completed hands use `GET /unions/{union_id}/hands/{hand_id}/replay?club_id={club_id}`. The response applies the same card-masking rules as local club replay; clients must never read raw Firestore audit documents.

The Angular lobby has separate Club Tables and Union Tables tabs, a union selector, approval queues for union admins, home-club seat labels, and union-code/theme branding. Club operators manage their own linked tables; the API, not the UI, remains the authority for each permission.

## Firestore records

```text
unions/{union_id}
  admins/{user_id}
  representatives/{user_id}
  clubs/{club_uuid}
  tables/{table_id}
    seats/{seat_index}
    hands/{hand_id}
  settlements/{YYYY-MM-DD}
    clubs/{club_uuid}
    hands/{hand_id}
union_codes/{union_code}
```

The founder and approval workflow create membership documents; direct client writes are not allowed. Union creation writes the active founder admin/representative and founding-club link. Representative and club links use `pending` until approved.

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

- Union creation, unique-letter lookup, representative approval, and club membership approval APIs are implemented. Union invitation email/notifications, rejection/withdrawal, and broader union role management remain undefined.
- Union table listing reflects tables running in the current process. Firestore stores table configuration and closed status, but the game engine does not recover active tables after restart.
- A seated table cannot be deleted. There is no leave-seat or cash-out route, so seats remain until those flows are designed and implemented.
- The shared WebSocket namespace and live table state work within one process. Scoped union lease claims use Firestore-backed `TableRuntime` methods when enabled, but gameplay writes are not atomically fenced and cross-instance routing, locking, recovery, and durable single-writer behavior are not implemented.
- The shared WebSocket namespace and live table state work within one process. Scoped union lease claims use Firestore-backed `TableRuntime` methods when enabled, but REST table operations are not owner-routed and cross-instance routing, locking, recovery, and durable single-writer behavior are not complete.
- Clearing positions are recorded by hand/day; club-to-club payout, reconciliation, cashier transfers, and automated daily close are not implemented.
- Rake collection and rake allocation are not implemented.

Before production use, provision Firestore security rules so clients cannot read Union audit, settlement, or private seat data directly. Configure exact frontend CORS origins with `CORS_ALLOWED_ORIGINS`. Validate multi-instance behavior and reconcile clearing ledgers in the Firestore emulator before enabling real balances.
