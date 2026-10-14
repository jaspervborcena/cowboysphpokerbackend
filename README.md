# Cowboys PH Poker Backend

Python 3.11+ backend starter built with FastAPI, PokerKit, WebSockets, and optional Google Cloud Firestore persistence.

## Project Layout

```text
base_poker_game.py       Core table and PokerKit hand behavior
database/                Hand-history and chip-ledger persistence
logic/                   WebSocket connections and table runtime orchestration
app/main.py              FastAPI app, REST routes, and WebSocket endpoint
tests/                   Engine and API integration tests
```

`BasePokerGame` is at the project root as requested. `database` owns persistence, `logic` owns live table coordination, and `app/main.py` wires those pieces to the API.

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
| `OWNER` | Club owner | Create tables and start hands. |
| `MANAGER` | Manager | Create tables and start hands. |
| `AGENT` | Agent | No role-specific API actions are currently assigned. |
| `PLAYER` | Player | Buy in, take a seat, and connect to play. |

All four roles require an active club membership. Role assignment itself is managed outside this API. See [API.md](API.md) for the client contract and route requirements.

Union administration is separate from club roles. `UNION_ADMIN` is assigned per union and can create/start shared tables and delete empty shared tables; club `OWNER` and `MANAGER` roles do not grant those permissions. Active `PLAYER` members join shared tables through their home club, and chip balances remain in that club's ledger. See [UNIONS.md](UNIONS.md) for the domain model and current limits.

## Completed

- FastAPI health check and routes to create tables, seat players, and start hands.
- PokerKit-backed no-limit Texas Hold'em with server-side turn checking and legal action validation.
- Per-player WebSocket state with opponents' private hole cards masked.
- Firebase ID-token verification and active club membership checks, with role-gated table creation and hand starts.
- Restricted Firestore hand audits with timestamped replay frames and per-player card filtering through the replay API.
- Union table discovery and creation, union-admin gating, cross-club seats/sockets, and idempotent daily club debt/credit clearing.
- Configurable CORS with local Angular origins allowed by default.
- Automatic turn timeout that checks when possible and folds when facing a bet.
- Firestore-backed buy-in reservation: verifies available member balance, moves the amount to in-play chips, and reserves a seat idempotently.
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

Protected routes require valid Firebase ID tokens, active Firestore membership records, and a working Firestore connection. If Firestore or Firebase verification is unavailable, protected routes fail closed; automated tests use fake verifiers and repositories and need no cloud account.

Completed hands are written to the restricted Firestore hand audit collection. Player clients must use `GET /clubs/{club_id}/hands/{hand_id}/replay`; the server masks opponents' cards unless they were shown at showdown. See [API.md](API.md) for the response contract and [database/README.md](database/README.md) for the audit schema.

Create a table with `POST /clubs/demo/tables/table-1` and JSON `{"small_blind":50,"big_blind":100,"buy_in_min":100,"buy_in_max":1000}`, using a Firebase ID token for an active `OWNER` or `MANAGER`. Seat players with `POST /clubs/demo/tables/table-1/seats`, for example `{"seat_index":0,"buy_in_amount":500,"buy_in_id":"00000000-0000-0000-0000-000000000001"}` and that player's token. Use a new UUID for each purchase and reuse it if retrying. The member document must have `status: "active"`, role `PLAYER`, and an available balance. Seat a second player, then start a hand as an active `OWNER` or `MANAGER`.

Connect each client to `ws://127.0.0.1:8000/ws/table/demo/table-1`. On socket open, first send `{"type":"authenticate","id_token":"<Firebase ID token>"}`, then wait for the `authenticated` response and initial state snapshot. Send `{"action":"FOLD"}`, `{"action":"CHECK"}`, `{"action":"CALL"}`, or `{"action":"RAISE","amount":300}`. Raise amounts are raise-to totals for the current betting round.

Run tests with `python -m pytest`.

## Firestore and Cloud Run

Firestore is disabled by default. To enable it, set `FIRESTORE_ENABLED=true`, set `GOOGLE_CLOUD_PROJECT`, and configure Application Default Credentials. Buy-ins use a transaction document under the member and a seat reservation under the table; hand audits are stored at `clubs/{club_id}/tables/{table_id}/hands/{hand_id}` and include private cards. Restrict these records to trusted server-side access.

See [database/README.md](database/README.md) for the collection layout, implemented and suggested document shapes, and field definitions.

Build and run with Docker, or deploy the container to Cloud Run with session affinity enabled (for example, `gcloud run deploy ... --session-affinity`).

## What's Still Missing

The following is a gap list, not functionality already present. The app is suitable for local integration work, but not public or real-money play.

### P0: Security and chip integrity

- **Authorization hardening:** Firebase tokens are verified and active membership plus initial role checks are enforced. Add comprehensive per-table/club policies, review role provisioning and revocation, and security-test every route/socket path before public use.
- **Cash-outs and reconciliation:** buy-in now checks and debits available member balance, creates an idempotent Firestore seat reservation, and records the amount as in-play. Cash-out, rebuy/top-up, refunds, and reconciliation are still missing. Do not use this as a complete wallet or real-money ledger until those flows exist.
- **Reliable settlement retries:** the runtime removes a completed hand from memory before the Firestore write returns. A failed write can therefore lose the in-process retry copy. Keep a durable pending settlement/outbox and clear it only after a successful, idempotent transaction.
- **Durable single-writer tables:** table state is process memory. Persist/recover active tables and use ownership/locking so two Cloud Run instances cannot accept actions for the same hand. Session affinity alone does not provide this guarantee.

### P1: Complete the playable table workflow

- **Table and seat lifecycle:** local/union table listing and union table closure exist, but add sit-out/sit-in, leave-seat, rebuy/top-up, cash-out, and host/dealer controls. Local club table metadata and all live table state remain in memory.
- **WebSocket reliability:** the socket now authenticates with a Firebase token on its first message. Still define versioned message types, action IDs, acknowledgements, and stable error codes; add heartbeat, reconnect/resync behavior, stale-connection cleanup, and backpressure handling. Game errors remain human-readable strings.
- **Game-state edge cases:** define and test all-in, side-pot, showdown, disconnected-player, timeout, and hand-end behavior. Current automatic timeout checks when possible and otherwise folds; there is no configurable sit-out or disconnect policy.
- **Firestore metadata and operations:** define schema versions/timestamps for local tables, provision union/admin/club membership documents, configure access rules/indexes, and test transactions with the Firestore emulator. The repository does not initialize member balances or recover live game state.

### P2: Production readiness and extensibility

- **Broader test coverage:** add tests for legal/illegal raise sizing, calls/checks, all-ins and side pots, timeout decisions, duplicate/stale actions, failed/retried settlement, chip conservation, and restart recovery. Current tests cover basic hand start, masking, turn enforcement, fold settlement, and one WebSocket flow.
- **Game selection:** the engine hardcodes No-Limit Texas Hold'em. Add a game configuration/adapter boundary before enabling other PokerKit variants; different variants need their own betting, action, and state-serialization rules.
- **Operations:** add structured logs, metrics/tracing, readiness checks, rate limits, validated environment settings, secret handling, CI, and deployment/rollback checks. Set `CORS_ALLOWED_ORIGINS` to the exact production frontend origins.

## Suggested Build Order

1. Harden role provisioning/revocation and test authorization rules across all routes and sockets.
2. Implement cash-out, rebuy, refunds, and settlement recovery with idempotent ledger movements.
3. Move table ownership/state to a durable single-writer design with restart recovery.
4. Complete table/seat lifecycle and version the WebSocket message contract for the UI.
5. Expand rules, failure, and integration tests; then add operational gates and additional game variants.

For Union table provisioning, endpoint flows, and shared settlement behavior, see [API.md](API.md). For persisted collection paths and document fields, see [database/README.md](database/README.md).