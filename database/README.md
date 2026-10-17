# Firestore Data Model

This document describes current Firestore writes and suggested metadata shapes. Club member balances represent the private club-managed money economy and are separate from the platform-wide play-money wallet. Buy-ins debit the member's home-club balance and reserve a table seat in a Firestore transaction. Completed hands are first written as pending settlement audits, then an idempotent transaction applies net results to in-play balances and marks each audit settled. Startup retries pending audits. Union table metadata and per-hand clearing records are written by the backend. Global play-money wallets and reserve-backed initial grants are implemented; global-table gameplay, local club/table metadata, general member initialization, external payouts/refunds, and live table recovery are not. Club wallet cash-in and claim-back are operator-mediated transfers between member balances, not external cash payments.

Target wallet accounts are distinct by purpose:

- `GLOBAL_PLAYMONEY`: a platform-level play-money balance shown on the landing page. It is not fiat/crypto and cannot be converted to club chips.
- `CLUB_TREASURY`: the shared club reserve controlled by authorized club operators.
- `CLUB_AGENT`: an agent's operating/distribution balance within one club.
- `CLUB_PLAYER`: a player's available balance within one club. The same Firebase user may have both `CLUB_AGENT` and `CLUB_PLAYER` accounts in that club.
- `TABLE_ESCROW`: chips committed to a seat/table while in play. Buy-in moves value from the player's club wallet into the table; cash-out returns it through a recorded transaction.

Each account must have an explicit `wallet_type`, scope, owner, and `currency_type`. Ledger transactions must identify source and destination accounts and balance per currency; never sum or implicitly transfer across global and club currencies. Cached balances may be maintained for reads, but ledger entries and account updates must commit atomically and idempotently.

These are the target account concepts, but not all have distinct account documents today. `GLOBAL_PLAYMONEY` has a persistent wallet document and reserve-backed initial grant. Club members still have one `chip_balance`/`in_play_balance` pair rather than separate `CLUB_AGENT`, `CLUB_PLAYER`, and `TABLE_ESCROW` accounts. Keep the current-field descriptions below as the existing club-member implementation until that account migration is designed and shipped.

## Collection Layout

```text
platform_wallets/global-playmoney-reserve
global_wallets/{user_id}
  transactions/{transaction_id}
clubs/{club_id}
  members/{user_id}
    transactions/{buy_in_id}
  transactions/{transaction_id}
  treasury/wallet
  treasury/pools
  tables/{table_id}
    seats/{seat_index}
    hands/{hand_id}
    runtime/state
      actions/{action_hash}
    runtime/lease
unions/{union_id}
  admins/{user_id}
  clubs/{club_id}
  tables/{table_id}
    seats/{seat_index}
    hands/{hand_id}
    runtime/state
      actions/{action_hash}
    runtime/lease
  settlements/{YYYY-MM-DD}
    clubs/{club_id}
    hands/{hand_id}
```

`clubs`, `members`, `tables`, and `hands` are Firestore collections/subcollections. The `{...}` portions are document IDs, not literal field values.

The repository provides Firestore transaction operations for `tables/{table_id}/runtime/state`, its `actions/{action_hash}` journal, and `tables/{table_id}/runtime/lease`. Checkpoints carry a schema version, sequence, owner ID, fencing epoch, and serialized state. Lease records carry owner, fencing generation, expiry, heartbeat, and status. These operations are unit-tested but are not called by table creation or `TableRuntime`; live table state and ownership remain process-local, and these documents may not exist for a running table.

## Fields Written by the Backend

### Member ledger

Path: `clubs/{club_id}/members/{user_id}`

`chip_balance` is the member's available off-table balance. `in_play_balance` tracks chips currently held at tables. A buy-in decreases `chip_balance` and increases `in_play_balance`; hand settlement applies `net_delta` only to `in_play_balance`. The sample includes profile metadata for context; the current repository changes the two balance fields but does not create or initialize member accounts.

```json
{
  "user_id": "player-a",
  "username": "Alice",
  "status": "active",
  "role": "PLAYER",
  "chip_balance": 12500,
  "in_play_balance": 500
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `user_id` | string | Application user ID; normally matches the document ID. |
| `username` | string | Display name. |
| `status` | string | Membership must be `active` for API access; other values are denied. |
| `role` | string | One of `OWNER`, `MANAGER`, `AGENT`, or `PLAYER`. `OWNER`, `MANAGER`, and `AGENT` can operate wallet transfers; agents are limited to assigned downline members. |
| `chip_balance` | integer | Available balance. Buy-in atomically subtracts the requested amount. |
| `in_play_balance` | integer | Chips currently held at tables. Buy-in adds the amount; hand settlement applies the player's net result here. |

Current limitation: a club member has one `role` and one `chip_balance`/`in_play_balance` pair. Agent send-out and claim-back operate on that same member balance, while table buy-in requires the `PLAYER` role. This does not support one Firebase identity acting as both an agent and a player with separate wallets. The target model requires separately scoped agent-operating and personal-player accounts, explicit transfer rules, and distinct audit/idempotency records; do not treat the current member balance as both wallets.

### Buy-in transaction and seat reservation

One Firestore transaction verifies the member exists, confirms sufficient available balance, checks the seat is free, debits the member, and creates both records below. The request's `buy_in_id` is the transaction document ID and makes a retry of the same purchase idempotent.

Transaction path: `clubs/{club_id}/members/{user_id}/transactions/{buy_in_id}`

```json
{
  "transaction_id": "00000000-0000-0000-0000-000000000001",
  "type": "buy_in",
  "table_id": "table-1",
  "seat_index": 0,
  "amount": 500,
  "chip_balance_delta": -500,
  "in_play_balance_delta": 500,
  "created_at": "Firestore server timestamp"
}
```

Seat path: `clubs/{club_id}/tables/{table_id}/seats/{seat_index}`

```json
{
  "seat_index": 0,
  "user_id": "player-a",
  "username": "Alice",
  "stack": 500,
  "buy_in_id": "00000000-0000-0000-0000-000000000001",
  "status": "sitting_in",
  "created_at": "Firestore server timestamp"
}
```

The seat document stores the reserved stack and the idempotency key. Hand settlement updates this seat document's `stack` to the hand's final stack. Full table recovery and seat cash-out/removal are not implemented yet.

The same Firestore transaction also creates a club-wide audit entry at `clubs/{club_id}/transactions/{buy_in_id}` with `currency_type: "CLUB_CHIPS"` and `type: "BUY_IN"`. Send-out and claim-back use this collection for double-entry transfer records. Club transaction writes are server-only.

### Club wallet and treasury

`POST /clubs/{club_id}/wallet/send-out` and `POST /clubs/{club_id}/wallet/claim-back` use Firestore transactions to update both member balances and write an idempotent audit entry at `clubs/{club_id}/transactions/{transaction_id}`. Agents can only transact with members assigned to them by `agent_id`. Claim-back is rejected if the member's `in_play_balance` is nonzero.

The shared club treasury wallet is stored separately at `clubs/{club_id}/treasury/wallet`:

```json
{
  "chip_balance": 2500
}
```

Platform provisioning through `POST /platform/clubs` creates the club root document, its initial active owner membership, this wallet with `chip_balance: 10000`, and `clubs/{club_id}/transactions/initial-funding` with `type: "INITIAL_GRANT"` in one Firestore transaction. Only Firebase users carrying the trusted `platform_admin: true` custom claim may create clubs. The supplied `owner_user_id` is not checked against Firebase Auth, so the caller must use a valid UID. Existing clubs are not automatically credited. The endpoint generates a new club ID for each request and has no request idempotency key; retrying an ambiguous request may create a second club and grant.

`GET /clubs/{club_id}/wallet` exposes this balance to active owners and managers. `POST /clubs/{club_id}/wallet/transfers` transfers funds from this wallet to another existing club's wallet. The source operator must be an active `OWNER` or `MANAGER`; the transaction checks the role and both club documents, debits and credits balances atomically, and writes matching `INTER_CLUB_SEND_OUT` / `INTER_CLUB_RECEIVE` records under each club's `transactions` collection. Newly provisioned clubs receive the initial 10,000 balance; older clubs are not automatically funded. Debit/credit card and PayPal funding are deferred; no client-supplied payment result can credit this balance.

The treasury aggregate is stored at `clubs/{club_id}/treasury/pools`:

```json
{
  "total_rake_collected": 0,
  "bad_beat_jackpot_pool": 0,
  "hand_of_the_day_pool": 0
}
```

For local club hands, rake and BBJ drops update these fields atomically with the hand audit and member settlement. Each contribution also gets a server-written club transaction entry keyed by hand ID and type, so a retried hand does not double-count pools. The Hand-of-the-Day field is reserved, but contributions and awards are not applied until their funding and winner rules are defined. Union-wide fee allocation remains undefined and is not applied to these club pools. Global play-money wallets are implemented, but global-table buy-ins are not.

### Completed hand audit

Path: `clubs/{club_id}/tables/{table_id}/hands/{hand_id}`

The document ID is the generated `hand_id`. This raw audit is server-only: it includes private hole cards, remaining/burn/mucked deck cards, first-party engine events, and timestamped replay frames. Never expose this document directly to a client. Player replay clients must use `GET /clubs/{club_id}/hands/{hand_id}/replay`, which masks cards for the requesting user.

```json
{
  "hand_id": "bfb74a70-1dad-4d8a-bf76-e192d23b50ee",
  "club_id": "demo",
  "table_id": "table-1",
  "started_at": "2026-10-14T10:00:00+00:00",
  "timestamp": "2026-10-14T10:00:30+00:00",
  "final_board": ["Ah", "7d", "2c", "Ks", "3h"],
  "pot_total": 400,
  "rake_amount": 0,
  "is_showdown": true,
  "winners": [{"user_id": "player-a", "amount": 400}],
  "pot_distributions": [{"pot_index": 0, "board_index": null, "hand_type_index": null, "amounts": [400, 0]}],
  "deck_state": {
    "remaining_cards": ["2s", "3s"],
    "burn_cards": ["4c", "5c", "6c"],
    "mucked_cards": [],
    "reserved_cards": []
  },
  "frames": [
    {
      "frame_index": 0,
      "timestamp": "2026-10-14T10:00:00+00:00",
      "street": 0,
      "community_cards": [],
      "pot": 150,
      "active_seat": 0,
      "last_action": null,
      "players": [
        {"user_id": "player-a", "username": "Alice", "seat": 0, "stack": 450, "hole_cards": ["As", "Ad"], "mucked": false, "in_hand": true},
        {"user_id": "player-b", "username": "Bob", "seat": 1, "stack": 400, "hole_cards": ["Kh", "Qc"], "mucked": false, "in_hand": true}
      ]
    },
    {
      "frame_index": 1,
      "timestamp": "2026-10-14T10:00:01+00:00",
      "street": 0,
      "community_cards": [],
      "pot": 400,
      "active_seat": 1,
      "last_action": {"user_id": "player-a", "action": "CALL", "amount": 50, "timestamp": "2026-10-14T10:00:01+00:00"},
      "players": [
        {"user_id": "player-a", "username": "Alice", "seat": 0, "stack": 300, "hole_cards": ["As", "Ad"], "mucked": false, "in_hand": true},
        {"user_id": "player-b", "username": "Bob", "seat": 1, "stack": 300, "hole_cards": ["Kh", "Qc"], "mucked": false, "in_hand": true}
      ]
    }
  ],
  "board": ["Ah", "7d", "2c", "Ks", "3h"],
  "players": [
    {
      "seat": 0,
      "user_id": "player-a",
      "username": "Alice",
      "starting_stack": 500,
      "final_stack": 600,
      "net_delta": 100,
      "hole_cards": ["As", "Ad"]
    },
    {
      "seat": 1,
      "user_id": "player-b",
      "username": "Bob",
      "starting_stack": 500,
      "final_stack": 400,
      "net_delta": -100,
      "hole_cards": ["Kh", "Qc"]
    }
  ],
  "operations": [
    {
      "type": "BlindOrStraddlePosting",
      "user_id": "player-a",
      "amount": 50
    },
    {
      "type": "HoleDealing",
      "user_id": "player-a",
      "cards": ["As", "Ad"]
    },
    {
      "type": "CheckingOrCalling",
      "user_id": "player-b",
      "amount": 50
    }
  ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `hand_id` | string | Unique hand ID and Firestore document ID. |
| `club_id` | string | Club owning the hand; used to scope replay access. |
| `table_id` | string | Table this hand was played at. |
| `started_at`, `timestamp` | string | UTC hand start and completion timestamps. |
| `board` | array of strings | Community cards dealt during the hand. |
| `final_board` | array of strings | Final public community cards, used by replay summary. |
| `pot_total`, `rake_amount` | integer | Total chips pushed to pots and amount not paid to players. |
| `settlement_status` | string | `pending` after the durable hand audit is enqueued; `settled` after player/pool ledger updates commit atomically. |
| `is_showdown`, `winners` | boolean, array | Whether hole-card showdown occurred and gross chip awards by winner. |
| `pot_distributions` | array | Gross award totals by player for the NLH hand. |
| `uncalled_returns` | array | Chips returned because no opponent matched a player's contribution. |
| `deck_state` | object | Remaining, burn, mucked, and reserved cards; restricted audit data. |
| `frames` | array | Timestamped post-action replay snapshots, including raw hole cards for filtering. |
| `players` | array of objects | Per-player seat, identity, stacks, net result, and hole cards. |
| `players[].starting_stack` | integer | Stack at the start of the hand. |
| `players[].final_stack` | integer | Stack after the hand completes. |
| `players[].net_delta` | integer | First-party settlement result (`final_stack - starting_stack`); this delta is applied to `in_play_balance`, not the available wallet. |
| `players[].hole_cards` | array of strings | Player's private cards. These must not be exposed through public client reads. |
| `operations` | array of objects | Ordered first-party engine event audit, including player IDs, amounts, and dealt cards where applicable. |

The repository checks whether the hand document already exists inside the Firestore transaction. If it exists, it skips the hand write, in-play balance deltas, and seat stack updates, preventing a retry from applying the same hand twice. Firestore security rules must deny direct client reads to `hands` documents; clients use only the authenticated replay endpoint.

### Union memberships and shared table metadata

Unions and their admin/member-club links must be provisioned outside this API. The backend reads these records to enforce scope:

Union admin path: `unions/{union_id}/admins/{user_id}`

```json
{"role":"UNION_ADMIN","status":"active"}
```

Participating club path: `unions/{union_id}/clubs/{club_id}`

```json
{
  "union_id": "union-1",
  "club_id": "club-a",
  "union_name": "Pinoy Poker Alliance",
  "status": "active"
}
```

`UNION_ADMIN` is a union-scoped role and is not a `ClubRole`. A club owner does not gain union administration rights unless their user ID has an active union admin record.

Union table path: `unions/{union_id}/tables/{table_id}`

The backend writes `game_id` with the union table metadata. The current create endpoint uses `no_limit_texas_holdem`; other catalog entries are not yet supported for table creation.

The union-admin create endpoint writes `union_id`, `table_id`, `game_id`, `table_name`, `origin_club_id`, blinds, buy-in bounds, `max_seats`, and `status: "open"`. Deleting an empty table sets `status: "closed"` and `closed_at`; table history remains available. Live engine state and lobby listing are still held in process memory and are not reconstructed from this document after restart.

Union seat path: `unions/{union_id}/tables/{table_id}/seats/{seat_index}`

The seat reservation includes the player's `club_id`. The buy-in transaction debits that club's `members/{user_id}` record and stores its idempotency transaction under the member's existing `transactions/{buy_in_id}` collection.

Example shared table metadata at `unions/union-1/tables/final-table`:

```json
{
  "union_id": "union-1",
  "table_id": "final-table",
  "game_id": "no_limit_texas_holdem",
  "table_name": "Alliance Main",
  "origin_club_id": "club-a",
  "small_blind": 10,
  "big_blind": 20,
  "buy_in_min": 100,
  "buy_in_max": 5000,
  "max_seats": 9,
  "status": "open"
}
```

Example seat records at `unions/union-1/tables/final-table/seats/{seat_index}` show two players whose balances remain in different home clubs:

```json
{
  "seat_index": 0,
  "user_id": "player-a",
  "club_id": "club-a",
  "username": "Alice",
  "stack": 500,
  "buy_in_id": "00000000-0000-0000-0000-000000000101",
  "status": "sitting_in",
  "created_at": "Firestore server timestamp"
}
```

```json
{
  "seat_index": 1,
  "user_id": "player-b",
  "club_id": "club-b",
  "username": "Bob",
  "stack": 500,
  "buy_in_id": "00000000-0000-0000-0000-000000000102",
  "status": "sitting_in",
  "created_at": "Firestore server timestamp"
}
```

At settlement, the restricted hand document at `unions/union-1/tables/final-table/hands/{hand_id}` retains each player's home-club attribution. For example:

```json
{
  "hand_id": "hand-20261015-001",
  "union_id": "union-1",
  "table_id": "final-table",
  "settlement_status": "settled",
  "players": [
    {
      "seat": 0,
      "user_id": "player-a",
      "club_id": "club-a",
      "starting_stack": 500,
      "final_stack": 650,
      "net_delta": 150,
      "hole_cards": ["As", "Ad"]
    },
    {
      "seat": 1,
      "user_id": "player-b",
      "club_id": "club-b",
      "starting_stack": 500,
      "final_stack": 350,
      "net_delta": -150,
      "hole_cards": ["Kh", "Qc"]
    }
  ]
}
```

This is a schema example, not a checked-in Firestore fixture. Hand audit documents include private card data and must remain server-only; WebSocket clients receive separately masked snapshots instead.

### Union hand settlement and clearing

Union hand audits live at `unions/{union_id}/tables/{table_id}/hands/{hand_id}`. In one Firestore transaction the backend writes the restricted hand audit, updates each player's home-club `in_play_balance`, updates union seat stacks, and writes a per-hand settlement marker. A retry of the same hand does not repeat those changes.

Daily aggregate paths are:

- `unions/{union_id}/settlements/{YYYY-MM-DD}/clubs/{club_id}` with integer `debt` and `credit` totals.
- `unions/{union_id}/settlements/{YYYY-MM-DD}/hands/{hand_id}` with the per-club net deltas and debt/credit positions.

For each club, `net_delta` is the sum of that club's players' hand results. Negative net delta increments `debt` by its absolute value; positive net delta increments `credit`. Current union settlement does not allocate a software fee or traffic-based rake split.

WebSocket connections, protocol envelopes, topics, and live snapshots are not stored as Firestore documents. The server keeps socket registrations and live table state in process memory; the persisted union hand audit and seat records are the durable records. Chat collections are not defined because chat is not implemented.

## Suggested Metadata Documents

The following document shapes are recommendations for future club/table CRUD and loading. They are **not** currently written by this backend.

### Club

Path: `clubs/{club_id}`

```json
{
  "name": "Cowboys PH",
  "owner_user_id": "owner-1",
  "status": "active",
  "created_at": "Firestore server timestamp"
}
```

### Table

Path: `clubs/{club_id}/tables/{table_id}`

```json
{
  "name": "Main Table",
  "game_variant": "no_limit_texas_holdem",
  "status": "open",
  "small_blind": 50,
  "big_blind": 100,
  "buy_in_min": 100,
  "buy_in_max": 1000,
  "max_seats": 9,
  "created_at": "Firestore server timestamp"
}
```

`game_variant` is a suggested field for future game selection. The app currently supports only `no_limit_texas_holdem`; see the game options in the root [README](../README.md).

## Important Data Rules

- Store chip values as integers in the smallest unit used by the game. Do not use floating-point amounts.
- Keep hand audits server-written and admin-only: they contain all players' private hole cards.
- Never trust a client-provided balance, hand result, or `net_delta`. The authoritative source is the server's completed first-party hand state.
- Use Firestore transactions for ledger changes and make settlement idempotent by hand ID.
- Cash-out is not implemented. Add an idempotent transaction that removes/releases the seat and moves its final stack from `in_play_balance` back to `chip_balance` before treating these fields as a complete wallet ledger.
- Firebase ID tokens are verified and active club membership is required. REST derives the user ID from the verified token; WebSockets authenticate in their first message. Review role assignments and Firestore rules before public use.
- Live table state is currently held in process memory; this schema does not yet provide restart recovery or multi-instance single-writer coordination.