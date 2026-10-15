# Cowboys PH Poker Backend

Python 3.11+ backend starter built with FastAPI, PokerKit, WebSockets, and optional Google Cloud Firestore persistence.

## Architecture

The live poker game protocol is websocket-first. Client actions, table state snapshots, and turn updates are exchanged over FastAPI WebSocket endpoints in `app/main.py`; gRPC is not the application protocol for gameplay.

The Google Cloud libraries in this repo use gRPC under the hood for Firebase and Firestore integration, but the actual game communication at the app layer is websocket-based. Keep the realtime table transport on WebSocket and treat Firestore/Firebase as infrastructure services rather than the live game protocol.

## Economy Tiers

The intended economy has two isolated balances: private club chips belong to each club's managed money economy, while global chips are platform play-money for public/global games and promotions. Club balances are stored separately from any global play-money balance and must never be interchangeable. Club cash-in and cash-out are operator-mediated: an `OWNER`, `MANAGER`, or assigned `AGENT` sends chips to a member or claims available chips back from them. Direct player cash-out and external payouts, refunds, and reconciliation are not implemented. Persistent global play-money wallets and global tables are also not implemented.

## Project Layout

```text
base_poker_game.py       Core table and PokerKit hand behavior
database/                Hand-history and chip-ledger persistence
logic/                   WebSocket connections and table runtime orchestration
app/main.py              FastAPI app, REST routes, and WebSocket endpoint
tests/                   Engine and API integration tests
```

`BasePokerGame` is at the project root as requested. `database` owns persistence, `logic` owns live table coordination, and `app/main.py` wires those pieces to the API.

The current WebSocket lifecycle, legacy and v1 message examples, and union-table behavior are documented in [websocket.md](websocket.md).
The UI workflow, Firebase session handling, role-aware screens, v1 socket messages, retries, heartbeats, and error display guidance are in [UI_GUIDE.md](UI_GUIDE.md).

## Club Roles

Club membership uses the role IDs `OWNER`, `MANAGER`, `AGENT`, and `PLAYER`. Load the signed-in member's authoritative role from `GET /clubs/{club_id}/members/me`. Show the assigned role as read-only; users cannot change their own role in the app.

Example active session:

```text
Role: PLAYER
User: joppy2024
ID: player-a
```

The role panel should also state: “Your current role is PLAYER. Role assignment is managed by the club.” Substitute the member's actual role in this message for other sessions.

| Role | Display name | Current backend access |
| --- | --- | --- |
| `OWNER` | Club owner | Create tables, start hands, and manage club wallet transfers. |
| `MANAGER` | Manager | Create tables, start hands, and manage club wallet transfers. |
| `AGENT` | Agent | Send chips to and claim chips back from assigned downline members. |
| `PLAYER` | Player | Buy in, take a seat, and connect to play. |

All four roles require an active club membership. Role assignment itself is managed outside this API. See [API.md](API.md) for the client contract and route requirements.

Union administration is separate from club roles. `UNION_ADMIN` is assigned per union and can create/start shared tables and delete empty shared tables; club `OWNER` and `MANAGER` roles do not grant those permissions. Active `PLAYER` members join shared tables through their home club, and chip balances remain in that club's ledger. See [UNIONS.md](UNIONS.md) for the domain model and current limits.

## Completed

- FastAPI health check and routes to create tables, seat players, and start hands.
- PokerKit-backed no-limit Texas Hold'em with server-side turn checking and legal action validation.
- Per-player WebSocket state with opponents' private hole cards masked.
- Opt-in v1 WebSocket envelopes for auth, player actions, pre-actions, errors, and snapshots; legacy socket messages remain supported.
- Firebase ID-token verification and active club membership checks, with role-gated table creation and hand starts.
- Restricted Firestore hand audits with timestamped replay frames and per-player card filtering through the replay API.
- Union table discovery and creation, union-admin gating, cross-club seats/sockets, and idempotent daily club debt/credit clearing.
- Configurable CORS with local Angular origins allowed by default.
- Numeric error-code enum with standardized, sanitized REST/WebSocket responses and trace-linked structured logs.
- V1 WebSocket action IDs with bounded in-process duplicate suppression, conflicting-ID errors, heartbeat PING/PONG, and idle connection cleanup.
- Automatic turn timeout that checks when possible and folds when facing a bet.
- Firestore-backed buy-in reservation: verifies available member balance, moves the amount to in-play chips, and reserves a seat idempotently.
- Operator-mediated club wallet send-out and claim-back transfers with role/downline checks and idempotent Firestore audit records.
- Owner/manager treasury balance and atomic inter-club treasury transfers with paired audit records; card and PayPal funding are not enabled yet.
- Platform-admin club provisioning creates the initial owner membership and grants 10,000 treasury chips exactly once.
- Firestore transaction to save a hand audit, apply in-play balance deltas, and update seat stacks idempotently by hand ID.
- Docker image suitable for Cloud Run, plus focused engine and WebSocket integration tests.

## Poker Games

The backend currently runs **No-Limit Texas Hold'em** only. The game type is selected directly in `base_poker_game.py`; the other PokerKit variants below are library capabilities and are not yet exposed by this API.

Available concrete PokerKit game factories in the installed version:

| Category | PokerKit variants |
| --- | --- |
| Hold'em | `FixedLimitTexasHoldem`, `NoLimitTexasHoldem`, `NoLimitShortDeckHoldem`, `NoLimitRoyalHoldem` |
| Omaha | `PotLimitOmahaHoldem`, `FixedLimitOmahaHoldemHighLowSplitEightOrBetter` |
| Stud | `FixedLimitSevenCardStud`, `FixedLimitSevenCardStudHighLowSplitEightOrBetter`, `FixedLimitRazz` |
| Draw | `NoLimitDeuceToSevenLowballSingleDraw`, `FixedLimitDeuceToSevenLowballTripleDraw`, `FixedLimitBadugi` |
| Other variants | `KuhnPoker`, `LeducHoldem`, `Numeral211Holdem`, `RhodeIslandHoldem`, `RoyalRhodeIslandHoldem` |

To add another game, make the game variant configurable, create its PokerKit state with the right blinds, betting structure, and automations, then adapt its legal actions and state serialization. Draw games and variants with different betting structures need different action/state handling; swapping only the PokerKit class is not sufficient. Add variant-specific tests before enabling a game through the API.

## Run Locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
$env:FIRESTORE_ENABLED = "true"
$env:FIREBASE_PROJECT_ID = "porpavordelivery"
$env:GOOGLE_CLOUD_PROJECT = "porpavordelivery"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:4200,http://127.0.0.1:4200"
gcloud auth application-default login
uvicorn app.main:app --reload
```

Health check: `http://127.0.0.1:8000/health`. Interactive API docs: `http://127.0.0.1:8000/docs`. UI integration contract: [API.md](API.md).

Club-protected routes require valid Firebase ID tokens, active Firestore membership records, and a working Firestore connection. `POST /platform/clubs` instead requires the trusted Firebase custom claim `platform_admin: true`; it does not require membership in an existing club. Assign that claim only through a trusted Firebase Admin process. If Firestore or Firebase verification is unavailable, protected routes fail closed; automated tests use fake verifiers and repositories and need no cloud account.

Completed hands are written to the restricted Firestore hand audit collection. Player clients must use `GET /clubs/{club_id}/hands/{hand_id}/replay`; the server masks opponents' cards unless they were shown at showdown. See [API.md](API.md) for the response contract and [database/README.md](database/README.md) for the audit schema.

Create a table with `POST /clubs/demo/tables/table-1` and JSON `{"small_blind":50,"big_blind":100,"buy_in_min":100,"buy_in_max":1000}`, using a Firebase ID token for an active `OWNER` or `MANAGER`. Seat players with `POST /clubs/demo/tables/table-1/seats`, for example `{"seat_index":0,"buy_in_amount":500,"buy_in_id":"00000000-0000-0000-0000-000000000001"}` and that player's token. Use a new UUID for each purchase and reuse it if retrying. The member document must have `status: "active"`, role `PLAYER`, and an available balance. Seat a second player, then start a hand as an active `OWNER` or `MANAGER`.

Connect each client to `ws://127.0.0.1:8000/ws/table/demo/table-1`. On socket open, first send `{"type":"authenticate","id_token":"<Firebase ID token>"}`, then wait for the `authenticated` response and initial state snapshot. Send `{"action":"FOLD"}`, `{"action":"CHECK"}`, `{"action":"CALL"}`, or `{"action":"RAISE","amount":300}`. Raise amounts are raise-to totals for the current betting round.

Run tests with `python -m pytest`.

## Firestore and Cloud Run

Firestore is disabled by default. To enable it, set `FIRESTORE_ENABLED=true`, set `GOOGLE_CLOUD_PROJECT`, and configure Application Default Credentials. Buy-ins use a transaction document under the member and a seat reservation under the table; hand audits are stored at `clubs/{club_id}/tables/{table_id}/hands/{hand_id}` and include private cards. Restrict these records to trusted server-side access.

See [database/README.md](database/README.md) for the collection layout, implemented and suggested document shapes, and field definitions.

Build and run with Docker, or deploy the container to Cloud Run with session affinity enabled (for example, `gcloud run deploy ... --session-affinity`).

## What's Still Missing

The following is a gap list, not functionality already present. The app is suitable for local integration work, but is not production-ready for the club-managed money economy.

### P0: Security and chip integrity

- **Authorization hardening:** Firebase tokens are verified and active membership plus initial role checks are enforced. Add comprehensive per-table/club policies, review role provisioning and revocation, validate the initial owner UID during platform club creation, add request idempotency to avoid duplicate 10,000-chip grants, and security-test every route/socket path before public use.
- **External cash-outs and reconciliation:** local buy-in, operator-mediated club send-out/claim-back, and idempotent hand settlement update Firestore club balances. External payouts, rebuy/top-up, refunds, and reconciliation are still missing; club balances must not be treated as a complete production money ledger until those flows exist.
- **Rake, BBJ, and promotions:** local tables support `OWNER`/`MANAGER` rake settings up to 10%, caps, no-flop-no-drop, BBJ fee/minimum-pot settings, and idempotent rake/BBJ pool entries. Rake and BBJ drops are deducted from winner awards and reconcile with player deltas. BBJ qualifying-hand evaluation/payout/reseed, Hand-of-the-Day funding and award distribution, union fee allocation, payout rounding policy, and winner announcements remain incomplete. A persistent global play-money wallet and global tables are not implemented.
- **Reliable settlement retries:** completed hand audits are persisted with `settlement_status: "pending"` before the idempotent balance/pool transaction; that transaction marks them `settled`, and startup retries any pending audits. The runtime also retains the completed hand until persistence succeeds. If Firestore is unavailable before the pending audit is written, only the live process has the retry copy, so durable table recovery and storage-independent buffering remain open.
- **Durable single-writer tables:** table state is process memory. Persist/recover active tables and use ownership/locking so two Cloud Run instances cannot accept actions for the same hand. Session affinity alone does not provide this guarantee.

### P1: Complete the playable table workflow

- **Table and seat lifecycle:** local/union table listing and union table closure exist, but add sit-out/sit-in, leave-seat, rebuy/top-up, cash-out, and host/dealer controls. Local club table metadata and all live table state remain in memory.
- **WebSocket reliability:** Firebase-first authentication, route-validated v1 topics, standardized numeric error responses, heartbeat PING/PONG, and idle cleanup are implemented; legacy messages remain supported. The UI still needs to adopt v1 and send action IDs. Action deduplication is only bounded process memory, so durable idempotency, bounded send queues, and load-tested backpressure behavior remain. Reconnects authenticate again and receive a full snapshot; no snapshot sequence/version or incremental-delta recovery exists.
- **Game-state edge cases:** define and test all-in, side-pot, showdown, disconnected-player, timeout, and hand-end behavior. Current automatic timeout checks when possible and otherwise folds; there is no configurable sit-out or disconnect policy.
- **Firestore metadata and operations:** define schema versions/timestamps for local tables, provision union/admin/club membership documents, configure access rules/indexes, and test transactions with the Firestore emulator. The repository does not initialize member balances or recover live game state.

### P2: Production readiness and extensibility

- **Broader test coverage:** add tests for legal/illegal raise sizing, calls/checks, all-ins and side pots, timeout decisions, duplicate/stale actions, failed/retried settlement, chip conservation, and restart recovery. WebSocket tests now cover legacy compatibility, v1 envelopes, union players from different clubs, topic mismatch rejection, and per-player card masking; performance, chat-isolation, and multi-worker behavior remain untested.
- **Game selection:** the engine hardcodes No-Limit Texas Hold'em. Add a game configuration/adapter boundary before enabling other PokerKit variants; different variants need their own betting, action, and state-serialization rules.
- **Operations:** add structured logs, metrics/tracing, readiness checks, rate limits, validated environment settings, secret handling, CI, and deployment/rollback checks. Set `CORS_ALLOWED_ORIGINS` to the exact production frontend origins.

## Suggested Build Order

1. Close the P0 money and authorization gaps: owner-UID validation and idempotent platform club creation, route/socket policy review, then external payout/refund/reconciliation rules.
2. Implement the single-writer lease, fencing, durable action journal, checkpoint/replay, and failover design in [websocket.md](websocket.md) before running multiple workers against live tables.
3. Have the Angular client adopt the opt-in v1 envelope while keeping legacy compatibility during rollout; send action IDs, answer heartbeats, and display generic numeric error states.
4. Complete the table/seat lifecycle: sit-out, leave-seat, rebuy/top-up, and cash-out.
5. Add chat only after defining authorization, moderation, rate limits, and bounded queues; then benchmark game latency under chat load and at the target table count.
6. Expand game/settlement failure tests and operational gates before enabling additional PokerKit variants.

For Union table provisioning, endpoint flows, and shared settlement behavior, see [API.md](API.md). For persisted collection paths and document fields, see [database/README.md](database/README.md).