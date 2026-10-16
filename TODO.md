# Project TODO

Last reviewed: 2026-10-16

This list tracks repository work, not just architecture proposals. A completed diagram or document does not mean its runtime components are implemented.

## Completed in the Repository

- [x] Expose the installed game catalog and mark only verified NLH as create-supported.
- [x] Add optional `game_id` to local and union table creation; default to NLH and reject unsupported IDs.
- [x] Include `game_id` in table listings, snapshots, create responses, and persisted union table metadata.
- [x] Add focused tests for catalog accuracy and `game_id` propagation/rejection.
- [x] Document the current backend, first-party engine target, UI contract, and Firebase/Firestore architecture.
- [x] Create the selected Firebase/Firestore draw.io architecture and remove the conflicting alternatives.
- [x] Verify the full test suite after the `game_id` implementation: 55 tests passed.
- [x] Add independent first-party NLH contracts, card/deck, dealing/privacy projection, hand evaluation, and pot/outcome helper modules with focused tests; these are not yet wired into live tables.
- [x] Re-run the full repository test suite after engine-helper work: 111 tests passed.

## Immediate: First-Party NLH Engine

- [ ] Write reviewed NLH example hands and expected outcomes, including edge cases, as behavioral fixtures.
- [x] Define framework-independent typed engine contracts for game configuration, player inputs, commands/actions, legal actions, events, outcomes, and state transitions.
- [x] Implement deterministic test randomness and a cryptographically secure production shuffle source in the first-party deck.
- [x] Implement the first-party 52-card model, shuffle, draw/burn tracking, and explicit deck exhaustion behavior.
- [x] Add pure NLH hole-card, flop/turn/river dealing and per-viewer hole-card masking helpers with deterministic tests.
- [x] Initialize first-party preflop state with button-relative dealing and a player-specific projection that masks unrevealed opponent cards.
- [ ] Add flop/turn/river state transitions and showdown reveal rules; integrate first-party projections into completed-hand records and live WebSocket snapshots.
- [ ] Implement NLH state transitions and rules: blinds, turn order, checks, calls, folds, raises, all-ins, street progression, and hand completion.
- [x] Implement a first-party NLH high-hand evaluator for five-to-seven cards with category and kicker ordering, including wheel straights; cover it with deterministic golden tests.
- [x] Implement pure NLH main/side-pot construction, folded-player eligibility, uncalled returns, odd-chip tie splitting, and gross award calculation with chip-conservation tests.
- [ ] Add unit, property-based, and simulation tests for legal/illegal actions, unique cards, side pots, ties, chip conservation, and reproducible failures.
- [ ] Integrate the owned engine with the existing table runtime without moving networking or Firestore I/O into the engine.
- [ ] Verify existing snapshots, replay privacy, hand audits, and idempotent Firestore settlement against the new engine.
- [ ] Replace PokerKit-based catalog and tests with the explicit first-party registry.
- [ ] Remove PokerKit imports and the `requirements.txt` dependency only after a repository-wide search and full test suite pass without PokerKit.

## UI: Can Proceed Now

- [ ] Implement Firebase sign-in, membership/role display, club and union lobbies, and NLH table creation.
- [ ] Implement buy-in/seat flows, current v1 WebSocket authentication/actions/pre-actions, snapshots, errors, wallet views, and replay using the documented contracts.
- [ ] Keep the client engine-agnostic: do not calculate outcomes, alter balances, expose hidden cards, or depend on PokerKit fields.
- [ ] Keep the selectable game list limited to entries with `create_supported: true`; currently this is NLH only.

The UI can proceed in parallel with the engine rewrite as long as the current REST, WebSocket, snapshot, and replay contracts remain stable. Coordinate and version any breaking contract changes before rollout. The frontend source is not in this backend repository.

## Before Multi-Worker Live Tables

- [ ] Design and implement one authoritative owner per table with durable leases and fencing generations.
- [ ] Persist accepted state transitions/checkpoints and recover table state, action deduplication, and turn deadlines after worker failure.
- [ ] Add reconnect replay cursors and durable action idempotency.
- [ ] Keep current live-table testing on one backend instance until ownership and recovery exist.
- [ ] Add Pub/Sub only when gateway and runtime workers are separated; define command routing, event/reply routing, ordering, retries, and deduplication. Pub/Sub is backend-internal and must not own game state or balances.
- [ ] Test Firestore contention, quotas, cost, recovery, and load before claiming multi-worker or 1,000-10,000-player capacity.

## Security, Economy, and Operations

- [ ] Define Firestore wallet accounts for `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, `CLUB_AGENT`, `CLUB_PLAYER`, and table escrow, with explicit owners, scopes, and currency types.
- [ ] Support one Firebase identity as both an `AGENT` and a `PLAYER` with separate club-scoped agent-operating and personal-player wallets/ledgers. Define role grants, transfer boundaries, idempotency, and audit rules; never merge these balances.
- [ ] On new account creation, provision a persistent `GLOBAL_PLAYMONEY` wallet and grant exactly 20,000 play-money chips once. Make account creation/grant idempotent and keep this balance separate from all club accounts.
- [ ] On club creation, grant exactly 10,000 `CLUB_CHIPS` for the owner once. Current provisioning seeds the club treasury with 10,000 while the owner's personal member balance starts at zero; confirm whether the intended grant is treasury-only or should credit the owner's separate `CLUB_PLAYER` wallet, then align behavior and audit records.
- [ ] Specify double-entry wallet movements for treasury-to-agent funding, agent-to-player distribution/claim-back, player buy-in to table escrow, hand settlement, and cash-out. Commit ledger entries and cached account balances atomically and idempotently.
- [ ] Define UI-facing wallet summary/history APIs and authorization before replacing the current balance response fields.
- [ ] Define per-table lifetime semantics: every table has an assigned scheduled close datetime; its lifetime may vary (for example, 6 or 24 hours) based on that assignment. The weekly club settlement cutoff is an additional close boundary, not the only expiration schedule.
- [ ] Choose the club timezone and exact boundary behavior; store the assigned deadline as an unambiguous `scheduled_close_at`, separate from `closed_at` (the actual time the table was closed), and expose/configure it through the appropriate table contract.
- [ ] Implement an idempotent expiration worker that stops new hands, applies a policy for hands still in progress, settles or explicitly carries each seated stack, releases seats safely, and preserves hand/ledger audit history. Do not delete live seats or reset balances without accounting for in-play chips.
- [ ] Test cutoff boundaries, delayed/retried expiration jobs, active-hand handling, and recovery when the service is unavailable at the cutoff.
- [ ] Validate platform club owner UIDs and add request idempotency to prevent duplicate initial grants.
- [ ] Review authorization for every REST route, WebSocket action, table, union, and administrative operation.
- [ ] Define and test seat exit, rebuy/top-up, cash-out/refund, and reconciliation policies before treating club balances as a complete money ledger.
- [ ] Add Firestore schema versions, access rules/indexes, emulator-backed transaction tests, and backup/restore drills.
- [ ] Add per-table metrics (mailbox depth, action latency, timer drift, lease renewals), alerts, rate limits, and deployment/rollback checks.

## Later Game Scope

- [ ] Add PLO/PLO5/PLO6 as validated configurations only after the NLH engine is stable; preserve Omaha's exact-two-hole/exact-three-board rule.
- [ ] Build Tongits as a separate engine with its own state, actions, meld evaluation, and scoring.
- [ ] Add tournament registration, an authoritative tournament clock, blind-level commands, eliminations, table balancing, prize settlement, and recovery as a separate domain service.
- [ ] Keep unverified games unavailable even if the game catalog lists them.
