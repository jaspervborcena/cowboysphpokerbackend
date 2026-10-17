# Cowboys PH Poker Backend

Python 3.11+ backend built with FastAPI, first-party NLH logic, WebSockets, and optional Google Cloud Firestore persistence.

## Architecture

The live poker game protocol is websocket-first. Client actions, table state snapshots, and turn updates are exchanged over FastAPI WebSocket endpoints in the game server app, `app/main.py`; gRPC is not the application protocol for gameplay. Firestore-backed membership and wallet REST endpoints also run as a separate FastAPI app at `app/wallet_api.py` and are deployed in the `cowboys-wallet-api` Cloud Run service.

The Google Cloud libraries in this repo use gRPC under the hood for Firebase and Firestore integration, but the actual game communication at the app layer is websocket-based. Keep the realtime table transport on WebSocket and treat Firestore/Firebase as infrastructure services rather than the live game protocol.

The selected production target is the Firebase/Firestore design in [GAME_SERVER_FIREBASE_ARCHITECTURE.drawio](GAME_SERVER_FIREBASE_ARCHITECTURE.drawio). Live NLH tables use the first-party engine through `logic/first_party_table.py`; PokerKit is no longer a runtime or test dependency.

## Economy Tiers

The target has separate wallet accounts for platform/global play-money, the club treasury, an agent's club operating balance, and a player's club balance. A user who is both an agent and a player has distinct agent and player accounts. Table stacks are separately tracked as in-play/escrow value. The global wallet is not convertible to club chips. The current implementation uses a platform reserve wallet as the authoritative source for initial grants: `ensure_global_wallet(...)` and `create_club(...)` debit the reserve and credit the destination wallet in one transaction, with ledger rows capturing `from_wallet_id`, `to_wallet_id`, `amount`, `currency_type`, and timestamps. Browser code may read these values but must never be the authority for money creation or treasury mutations. Firestore still has one `chip_balance`/`in_play_balance` pair per club member rather than separate agent/player accounts. Direct player cash-out, external payouts/refunds, and reconciliation are still not implemented.

## Project Layout

```text
logic/table_game_base.py    Shared table metadata, seats, and accounting
logic/first_party_table.py  Live adapter for the first-party NLH engine
database/                Hand-history and chip-ledger persistence
logic/                   WebSocket connections and table runtime orchestration
app/main.py              Game server FastAPI app, table REST routes, and WebSockets
app/wallet_api.py        Standalone wallet REST API entrypoint
app/api/wallets.py       Shared membership and wallet REST routes
Dockerfile.wallet-api    Wallet API Cloud Run image
tests/                   Engine and API integration tests
```

`database` owns persistence, `logic` owns live table coordination and game state, and `app/main.py` wires those pieces to the API.

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
- First-party no-limit Texas Hold'em with server-side turn checking, legal action validation, side pots, and settlement outcomes.
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
- Platform-admin club provisioning creates the initial owner membership and grants 10,000 treasury chips exactly once through the reserve-backed accounting path.
- Reserve-backed initial grants for global play-money and club treasury are recorded with `from_wallet_id`/`to_wallet_id` audit rows and atomic balance updates.
- Firestore transaction to save a hand audit, apply in-play balance deltas, and update seat stacks idempotently by hand ID.
- Docker image suitable for Cloud Run, plus focused engine and WebSocket integration tests.

## Game Engine Direction

The current backend runs **No-Limit Texas Hold'em only**, using the first-party engine. The explicit `/games` registry lists only implemented, create-supported games.

**Decision:** use a first-party game engine that we can maintain and debug. No PokerKit source is copied or imported, and it is not a project dependency.

| Priority | First-party game scope | Notes |
| --- | --- | --- |
| 1 | No-Limit Texas Hold'em | Replace the current engine first; keep existing REST/WebSocket and Firestore contracts stable where possible. |
| 2 | Pot-Limit Omaha family | Add PLO, PLO5, and PLO6 as validated configurations only after the NLH engine is stable; preserve Omaha's exact-two-hole/exact-three-board evaluation rule. |
| 3 | Tongits | Build as an independent engine with its own state, actions, meld evaluation, and scoring; do not force it into poker betting abstractions. |
| Later | Other variants | Add only when selected, specified, implemented, and covered by their own rule and integration tests. |

The game catalog is an explicit first-party registry and lists only engines this server implements and supports for table creation.

## Game Server Responsibilities

The boundaries below describe the target design. NLH rules live in first-party, pure Python engine modules, independent of FastAPI, WebSockets, Firebase, and Firestore.

| Component | Owns | Must not own |
| --- | --- | --- |
| API and identity | REST/WebSocket input, authentication, authorization, schema validation, protocol versioning | Poker rules, card dealing, or balance calculations |
| Table coordinator | Table lifecycle, seat membership, per-table serialization/locking, turn timers, action idempotency, calls into a game engine | Hand ranking or durable money accounting |
| Game registry/configuration | Stable `game_id` to owned engine mapping; validated blinds, limits, variant options, and supported status | Mutable state shared between tables or arbitrary client-provided rules |
| Randomness and deck | Secure shuffle source, deck creation, draw/burn operations; injectable deterministic randomness for tests | Betting decisions, hand ranking, or winner selection |
| Dealing and visibility | Private hole cards, public board cards, burns/mucks, and recipient-specific card visibility | Whether an action is legal or who wins |
| Game state | Authoritative facts for a hand/table: seats, stacks, street, button, cards, contributions, actor, and status | Rules decisions or database writes |
| Rules and transitions | First-party validation and legal state transitions for a specific variant, including blinds, betting rounds, raises, all-ins, and hand completion | Transport, persistence, or treasury policy |
| Hand evaluation | Compare hands according to the variant's ranking rules, including high/low or other split evaluations where applicable | Pot construction, rake, or ledger updates |
| Pot and outcome resolution | Build main/side pots from contributions, determine eligible players, compare evaluations, resolve ties, and calculate gross awards | Updating player wallets or club treasury documents |
| Settlement and treasury | Apply rake/fees/promotional contributions and gross awards to chip ledgers atomically and idempotently; enforce chip conservation | Deciding legal poker actions or evaluating hands |
| Persistence, audit, and replay | Durable hand records, settlement status, action/replay history, recovery checkpoints; restrict private audit data | Being the live rules engine or exposing raw private cards to clients |
| Snapshot projection | Convert authoritative state into a complete, player-specific view with hidden cards masked | Mutating game state or deciding outcomes |
| Operations and testing | Metrics, structured logs, health/readiness, load tests, deterministic simulations, rules conformance, and failure/recovery tests | Making game decisions at runtime |

An action should flow through authenticated transport to the table coordinator, then through the variant rules/transition engine. The engine uses the deck and evaluator as needed and produces a completed-hand outcome; a separate settlement service commits chip and treasury changes. Persistence records the audit, while snapshot projection creates each player's safe view. The game engine should emit domain outcomes rather than writing directly to Firestore.

For any new variant, verify the complete path: deterministic setup, legal actions, dealing, state transitions, evaluation, side-pot/tie awards, replay serialization, and idempotent settlement. Only then should the registry advertise it as create-supported.

### Current Status and Gaps

| Area | Already in this repository | Still needed for the selected architecture |
| --- | --- | --- |
| Identity and access | Firebase ID-token verification, active membership checks, role-gated REST/WebSocket flows | Full route/table policy review, rate limits, production security review, and operational controls |
| Client transport | FastAPI REST, legacy and v1 WebSocket protocols, player-specific masked snapshots | Cross-process gateway-to-owner routing, durable replay cursor, reconnect event replay, and load-tested delivery/backpressure |
| Table runtime | In-process table map, per-table `asyncio.Lock`, turn timeout, bounded in-memory action deduplication | Single authoritative worker per table, leases/fencing, durable action IDs/state versions, checkpoint/recovery, timer recovery |
| Game engine | Live NLH actions, snapshots, replay frames, entry rules, straddles, and hand outcomes use the first-party engine. Unit, property, simulation, API, and audit tests cover the rules and contracts. | Verify settlement with the Firestore emulator before production rollout |
| Settlement and economy | Firestore buy-in transactions, hand audits, idempotent hand settlement, club pools, treasury transfers, union clearing | Durable settlement intent/outbox worker, replayable retries, conservation/reconciliation checks, explicit buy-out lifecycle |
| Persistence | Firestore repositories for current ledgers, audits, and union records | Durable table ownership, event/checkpoint schema, recovery procedures, transaction/contention tests, and a single authority per record type |
| Operations | Sanitized error codes, trace-linked logs, Docker/Cloud Run setup | Per-table metrics, alerts, load/soak tests, backup/restore drills, safe rollout and rollback |

PokerKit is no longer used by the backend. Firestore emulator and production-like transaction verification remain required before enabling real-money settlement.

#### New-Player NLH Entry

The button is not moved to accommodate a joining player. A new player in the small-blind seat is dealt in and posts the natural small blind. Elsewhere they wait for the big blind unless they explicitly accept a live big-blind post at seating; the consent is cleared once they enter a hand. A waiting player may occupy the button seat without being dealt, and the button remains there.

#### Straddle Mode Rules

Ring-game hand starts may specify one straddler: the player immediately after the big blind posts `2 * big_blind`. Preflop action starts after the straddler and the minimum raise increment becomes `2 * big_blind`. Short straddles, heads-up straddles, and tournament-mode straddles are rejected. Tournament gameplay is not implemented.

### First-Party NLH Replacement Plan

1. Freeze the current client and hand-audit contracts. Write explicit, independently reviewed expected outcomes for representative hands and edge cases so the replacement has a behavioral target.
2. Define typed engine contracts for configuration, state, actions, legal actions, domain events, completed-hand outcomes, and engine errors. Inject deterministic randomness in tests and a cryptographically secure source in production.
3. Implement the NLH core as pure game logic: card/deck model, shuffle/deal/burn, betting and turn transitions, hand ranking, main/side-pot eligibility, tie splitting, and gross awards. Keep treasury policy and Firestore writes outside the engine.
4. Add deterministic unit, property-based, and simulation tests for card uniqueness, legal/illegal actions, all-ins, side pots, ties, chip conservation, and reproducible failures.
5. Integrate the owned engine behind the table runtime while preserving authorization, snapshots, settlement idempotency, and replay privacy. Do not run two engines as competing authorities for live money state.
6. Verify idempotent Firestore settlement, audit records, and retries with the emulator before production rollout.
7. Stabilize own-engine NLH before starting PLO variants or the separate Tongits engine. Keep those variants unavailable until each has complete rules, evaluation, settlement, replay, and security tests.

The Firebase diagram describes the production target, not a two-week capacity guarantee. For an initial test deployment, keep the game scope to NLH and validate the engine and Firebase transaction behavior before claiming multi-worker or 1,000–10,000-player readiness.

## Run Locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
Copy-Item .env.example .env
# Set FIRESTORE_ENABLED=true in .env for Firebase/Firestore-backed workflows.
$env:CLOUDSDK_PYTHON = (Get-Command python).Source
gcloud.cmd auth application-default login --project=porpavordelivery
uvicorn app.main:app --reload --env-file .env
```

The local `.env` is ignored by Git. Keep credentials out of it; Google Application Default Credentials are stored separately by `gcloud`. `python-dotenv` is required for Uvicorn's `--env-file` option.

Health check: `http://127.0.0.1:8000/health`. Interactive API docs: `http://127.0.0.1:8000/docs`. UI integration contract: [API.md](API.md).

### Run the Wallet REST API Separately

The Firestore-backed membership and wallet routes can run independently of the live game server:

```powershell
$env:FIRESTORE_ENABLED = "true"
$env:FIREBASE_PROJECT_ID = "porpavordelivery"
uvicorn app.wallet_api:app --reload --host 127.0.0.1 --port 8001
```

The standalone service exposes only the member, club-wallet, and global-wallet REST routes plus `/health`; live table and WebSocket routes remain on `app.main:app`. The currently deployed Cloud Run service is `cowboys-wallet-api` in `asia-east1`: `https://cowboys-wallet-api-4rusyqoipa-de.a.run.app`. Its Swagger UI is at `/docs` and its OpenAPI contract at `/openapi.json`. Use separate Cloud Run services to deploy/restart these processes independently. The standalone API uses the Cloud Run service account's Firestore permissions and Firebase project ID from environment settings.

For a Cloud Run image, create the Artifact Registry repository `poker-services`, then build with `gcloud builds submit --project=porpavordelivery --config=cloudbuild.wallet-api.yaml .`. Deploy the resulting `asia-east1-docker.pkg.dev/porpavordelivery/poker-services/wallet-api:latest` image as a separate service with `FIRESTORE_ENABLED=true`, `FIREBASE_PROJECT_ID=porpavordelivery`, and `GOOGLE_CLOUD_PROJECT=porpavordelivery`. Permit unauthenticated Cloud Run ingress for browser access; the wallet routes still require a valid Firebase ID token. This deploy path does not seed Firestore.

### Firestore Emulator Settlement Test

Install JDK 21 or newer, then run the focused settlement test against the local Firestore emulator:

```powershell
firebase emulators:exec --only firestore --project demo-cowboys-ph-poker ".venv\Scripts\python.exe -m pytest tests\test_firestore_emulator.py -q"
```

The test uses an isolated generated club/table/hand ID and verifies uncalled-return audit data, member in-play balances, final seat settlement, and idempotent retry behavior. It skips during the regular test suite unless `FIRESTORE_EMULATOR_HOST` is set by `firebase emulators:exec`.

The Firestore settlement and demo-seed emulator tests also run in `.github/workflows/firestore-emulator.yml` on pushes and pull requests to `main`. CI installs JDK 21 and uses the `demo-cowboys-ph-poker` project ID; it does not connect to or write the production Firestore project.

### Firestore Demo Seed

Load a connected demo dataset into the local Firestore emulator only:

```powershell
firebase emulators:exec --only firestore --project demo-cowboys-ph-poker ".venv\Scripts\python.exe -m pytest tests\test_firestore_demo_seed.py -q"
```

The emulator test inserts the stable `demo-*` documents, reads them back, and checks the reserve/grant, club/member, union/club, and settlement/hand links. The seeder refuses non-local Firestore hosts and non-demo project IDs. The fixture covers the platform reserve/grant example, global wallet, club members and treasury, table audit, union membership/table, and settlement records. They are demo records, not live table state; the game server does not recover a playable table from seeded table documents. The runtime reserve-backed model uses the platform reserve as the funding source for initial player and club grants and records the corresponding `from_wallet_id`/`to_wallet_id` accounting rows.

To load the fixture without running the assertions, use `.venv\Scripts\python.exe -m database.seed_demo_firestore` as the command passed to `firebase emulators:exec`.

Club-protected routes require valid Firebase ID tokens, active Firestore membership records, and a working Firestore connection. `POST /platform/clubs` instead requires the trusted Firebase custom claim `platform_admin: true`; it does not require membership in an existing club. Assign that claim only through a trusted Firebase Admin process. If Firestore or Firebase verification is unavailable, protected routes fail closed; automated tests use fake verifiers and repositories and need no cloud account.

Completed hands are written to the restricted Firestore hand audit collection. Player clients must use `GET /clubs/{club_id}/hands/{hand_id}/replay`; the server masks opponents' cards unless they were shown at showdown. See [API.md](API.md) for the response contract and [database/README.md](database/README.md) for the audit schema.

### UI Table Creation Contract

The frontend's table template/defaults are UI-owned; the backend does not read `src/app/data/table-creation-defaults.json`. For a name-only Regular NLH table, the UI may use the template defaults for blinds and buy-in bounds, then call:

```text
POST /clubs/{club_id}/tables/{table_id}
Authorization: Bearer <Firebase ID token for an active OWNER or MANAGER>
Content-Type: application/json
```

The **current backend request schema supports six fields**:

```json
{
	"game_id": "no_limit_texas_holdem",
	"small_blind": 1,
	"big_blind": 2,
	"buy_in_min": 50,
	"buy_in_max": 1000,
	"table_size": 9
}
```

`table_id` is supplied in the URL, not the body. Slugify the entered name for this ID and ensure it is unique within the club. The current route does not accept a display name: it sets `table_name` to `table_id`, so retain the original friendly name in the UI if it should display differently. `game_id` is optional and defaults to `no_limit_texas_holdem`; it is the only game currently supported for creation, and unsupported IDs are rejected. `table_size` is optional for backward compatibility; when omitted, the server uses `MAX_SEATS`. When supplied, it sets this table's seat capacity and must be at least 2 and no greater than the configured `MAX_SEATS` ceiling (default `9`). The response is `201` with `{"club_id":"...","table_id":"...","game_id":"no_limit_texas_holdem","status":"created"}`.

Other game variants, mode, fees, restrictions, and advanced template options are still unsupported by the backend table engine. Do not offer catalog games with `create_supported: false` as playable choices. Preview-room tables are local-only and should not call this endpoint.

Example browser request:

```typescript
const idToken = await firebaseUser.getIdToken();
const response = await fetch(
	`${apiBase}/clubs/${encodeURIComponent(clubId)}/tables/${encodeURIComponent(tableId)}`,
	{
		method: 'POST',
		headers: {
			Authorization: `Bearer ${idToken}`,
			'Content-Type': 'application/json',
		},
		body: JSON.stringify({
			game_id: 'no_limit_texas_holdem',
			small_blind: 1,
			big_blind: 2,
			buy_in_min: 50,
			buy_in_max: 1000,
			table_size: 9,
		}),
	},
);
```

After the table is created, seat each player with `POST /clubs/{club_id}/tables/{table_id}/seats`, for example `{"seat_index":0,"buy_in_amount":500,"buy_in_id":"00000000-0000-0000-0000-000000000001"}` and that player's token. Use a new UUID for each purchase and reuse it only when retrying that purchase. The member document must have `status: "active"`, role `PLAYER`, and an available balance. Seat a second player, then start a hand as an active `OWNER` or `MANAGER`.

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
- **Durable single-writer tables:** table state is process memory. Keep live-table tests on one backend instance for now. Before multiple runtime workers, add durable table ownership/fencing, recovery, and cross-process command routing; Pub/Sub is deferred until that split exists. Session affinity alone does not provide this guarantee.

### P1: Complete the playable table workflow

- **Table and seat lifecycle:** local/union table listing and union table closure exist, but add sit-out/sit-in, leave-seat, rebuy/top-up, cash-out, and host/dealer controls. Local club table metadata and all live table state remain in memory.
- **WebSocket reliability:** Firebase-first authentication, route-validated v1 topics, standardized numeric error responses, heartbeat PING/PONG, and idle cleanup are implemented; legacy messages remain supported. The UI still needs to adopt v1 and send action IDs. Action deduplication is only bounded process memory, so durable idempotency, bounded send queues, and load-tested backpressure behavior remain. Reconnects authenticate again and receive a full snapshot; no snapshot sequence/version or incremental-delta recovery exists.
- **Game-state edge cases:** all-ins, side pots, showdowns, timeouts, and hand-end conservation are covered by the first-party NLH tests. Configurable sit-out, disconnect policy, and process-restart recovery remain open.
- **Firestore metadata and operations:** define schema versions/timestamps for local tables, provision union/admin/club membership documents, configure access rules/indexes, and test transactions with the Firestore emulator. The repository does not initialize member balances or recover live game state.

### P2: Production readiness and extensibility

- **Broader test coverage:** add tests for legal/illegal raise sizing, calls/checks, all-ins and side pots, timeout decisions, duplicate/stale actions, failed/retried settlement, chip conservation, and restart recovery. WebSocket tests now cover legacy compatibility, v1 envelopes, union players from different clubs, topic mismatch rejection, and per-player card masking; performance, chat-isolation, and multi-worker behavior remain untested.
- **First-party game engine:** NLH rules, runtime integration, catalog, and dependency migration are complete. Verify Firestore settlement with the emulator before production rollout. Do not enable PLO or Tongits from catalog metadata alone.
- **Operations:** add structured logs, metrics/tracing, readiness checks, rate limits, validated environment settings, secret handling, CI, and deployment/rollback checks. Set `CORS_ALLOWED_ORIGINS` to the exact production frontend origins.

### Current Work Plan

- Verify idempotent hand settlement and audit fields with the Firestore emulator, including retries and uncalled returns.
- Continue UI implementation against the stable REST and WebSocket contracts. Keep the frontend game-engine agnostic, offer NLH only, and do not connect the UI to Pub/Sub.
- For live-table testing, run one backend instance until durable table ownership and recovery exist.

### Before Multi-Worker Runtime

- Add table ownership leases with fencing, durable events/checkpoints, recovery of turn deadlines, action deduplication, and reconnect replay.
- When gateway and runtime workers are separated, add Pub/Sub for internal command/event forwarding. Define ordering, retries, deduplication, and reply routing; Pub/Sub must not become the state or balance authority.
- Load-test Firestore transaction contention, quotas, recovery, and cost before claiming multi-worker or 1,000–10,000-player readiness.

### Later

- Close remaining security and economy gaps: owner-UID validation, idempotent platform club provisioning, full authorization review, cash-out/refund/reconciliation policy, and operational monitoring.
- Add PLO/PLO5/PLO6 and Tongits only as separate verified engine milestones. Keep tournament scheduling and balancing in a separate tournament service, not in hand rules.

For Union table provisioning, endpoint flows, and shared settlement behavior, see [API.md](API.md). For persisted collection paths and document fields, see [database/README.md](database/README.md).