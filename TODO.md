# Project TODO

Last reviewed: 2026-10-17

This list tracks repository work, not just architecture proposals. A completed diagram or document does not mean its runtime components are implemented.

## Completed in the Repository

- [x] Implement reserve-backed initial grants for the global play-money wallet and club treasury, with atomic reserve debit + destination credit + ledger logging.
- [x] Add reserve-backed accounting metadata (`from_wallet_id`, `to_wallet_id`, `amount`, `currency_type`, and timestamps) to initial funding records and keep retries idempotent.
- [x] Replace installed-package game discovery with an explicit first-party registry listing only verified NLH as create-supported.
- [x] Add optional `game_id` to local and union table creation; default to NLH and reject unsupported IDs.
- [x] Include `game_id` in table listings, snapshots, create responses, and persisted union table metadata.
- [x] Add focused tests for catalog accuracy and `game_id` propagation/rejection.
- [x] Document the current backend, first-party engine target, UI contract, and Firebase/Firestore architecture.
- [x] Create the selected Firebase/Firestore draw.io architecture and remove the conflicting alternatives.
- [x] Verify the full test suite after the `game_id` implementation: 55 tests passed.
- [x] Add independent first-party NLH contracts, card/deck, dealing/privacy projection, hand evaluation, and pot/outcome helper modules with focused tests.
- [x] Route newly created club and union tables through the first-party NLH adapter while preserving table snapshots, replay frames, and hand-audit settlement fields.
- [x] Complete the first-party NLH live path, entry/straddle options, explicit registry, and PokerKit removal; full suite passes (145 passed, 1 emulator test skipped).
- [x] Re-run the full repository test suite after engine-helper work: 111 tests passed.

## Immediate: First-Party NLH Engine

- [x] Write reviewed NLH example hands and expected outcomes, including edge cases, as behavioral fixtures.
- [x] Define framework-independent typed engine contracts for game configuration, player inputs, commands/actions, legal actions, events, outcomes, and state transitions.
- [x] Implement deterministic test randomness and a cryptographically secure production shuffle source in the first-party deck.
- [x] Implement the first-party 52-card model, shuffle, draw/burn tracking, and explicit deck exhaustion behavior.
- [x] Add pure NLH hole-card, flop/turn/river dealing and per-viewer hole-card masking helpers with deterministic tests.
- [x] Initialize first-party preflop state with button-relative dealing and a player-specific projection that masks unrevealed opponent cards.
- [x] Add first-party flop/turn/river state transitions and showdown reveal rules, including folded-card privacy.
- [x] Integrate first-party projections into completed-hand records and live WebSocket snapshots when the table runtime switches to the owned engine.
- [x] Implement NLH state transitions and rules: blinds, turn order, checks, calls, folds, raises, all-ins, street progression, and hand completion.
- [x] Add no-buy-the-button new-player entry rules: natural small-blind entry, wait for the big blind, or accept a live big-blind post.
- [x] Add one live UTG straddle for ring games; keep tournament-mode hands on separate no-straddle rules.
- [x] Implement a first-party NLH high-hand evaluator for five-to-seven cards with category and kicker ordering, including wheel straights; cover it with deterministic golden tests.
- [x] Implement pure NLH main/side-pot construction, folded-player eligibility, uncalled returns, odd-chip tie splitting, and gross award calculation with chip-conservation tests.
- [x] Add unit, property-based, and simulation tests for legal/illegal actions, unique cards, side pots, ties, chip conservation, and reproducible failures.
- [x] Integrate the owned engine with the existing table runtime without moving networking or Firestore I/O into the engine.
- [x] Verify snapshots, replay privacy, hand audits, action idempotency, and in-process settlement behavior against the new engine.
- [x] Replace the PokerKit-based catalog and tests with the explicit first-party registry.
- [x] Remove PokerKit imports and the `requirements.txt` dependency after a repository-wide search and full test suite pass without PokerKit.
- [x] Add a Firestore emulator config and a focused first-party NLH settlement/idempotency integration test.
- [x] Add versioned JSON checkpoint codecs for NLH hand/deck state and the enclosing table state, including seats, settings, button rotation, pre-actions, replay frames, engine events, and pending completed hands; persistence remains outstanding.
- [x] Add a Firestore transaction primitive that initializes versioned table checkpoints and atomically advances a checkpoint with a user/action-ID journal record, guarded by expected sequence and fencing epoch.
- [ ] Run the Firestore emulator settlement test before production rollout. Firebase CLI requires JDK 21; the current environment only has JDK 17.

## UI: Can Proceed Now

- [ ] Implement Firebase sign-in, membership/role display, club and union lobbies, and NLH table creation.
- [ ] Implement buy-in/seat flows, current v1 WebSocket authentication/actions/pre-actions, snapshots, errors, wallet views, and replay using the documented contracts.
- [ ] After Firebase sign-up/sign-in, call `POST /me/wallets/global-playmoney/ensure` to provision the authenticated user's persistent `GLOBAL_PLAYMONEY` wallet with 20,000 chips, then load it with `GET /me/wallets/global-playmoney`. Keep it isolated from club chips and do not offer client-controlled grant amounts.
- [ ] After club provisioning, display the live 10,000-chip `CLUB_TREASURY` grant from `POST /platform/clubs` and refresh via `GET /clubs/{club_id}/wallet`. The owner membership's personal `chip_balance` currently starts at zero; do not show the treasury grant as personal funds.
- [ ] For club players, use the live `POST /clubs/{club_id}/wallet/send-out` flow for funding by an authorized owner, manager, or assigned agent. There is no other club-funding path for players.
- [ ] Mock a player-initiated cash-out request as pending UI state. An authorized owner, manager, or assigned agent can fulfill it with `POST /clubs/{club_id}/wallet/claim-back`; surface the existing restriction that claim-back is unavailable while `in_play_balance` is nonzero. No request/approval API exists yet.
- [ ] Show available and in-play member balances separately, and keep global play money, club treasury funds, operator funds, player funds, and table stacks distinct.
- [ ] Keep the client engine-agnostic: do not calculate outcomes, alter balances, expose hidden cards, or depend on engine internals.
- [ ] Keep the selectable game list limited to entries with `create_supported: true`; currently this is NLH only.

The UI can proceed against the stable REST, WebSocket, snapshot, and replay contracts. Coordinate and version any breaking contract changes before rollout. The frontend source is not in this backend repository.

## Before Multi-Worker Live Tables

- [x] Add a Firestore transaction primitive that atomically advances a table checkpoint and records the action fingerprint, guarded by expected sequence and fencing epoch.
- [ ] Design and implement one authoritative owner per table with durable leases and fencing generations.
- [ ] Wire checkpoint/action transactions into table creation, seating, hand start, player actions, pre-actions, and timeout actions. Stage mutations and publish/acknowledge only after commit; atomically couple buy-in debits with seat/checkpoint updates.
- [ ] Add protected/encrypted checkpoint storage and turn-deadline recovery.
- [ ] Add reconnect replay cursors and durable action idempotency.
- [ ] Keep current live-table testing on one backend instance until ownership and recovery exist.
- [ ] Add Pub/Sub only when gateway and runtime workers are separated; define command routing, event/reply routing, ordering, retries, and deduplication. Pub/Sub is backend-internal and must not own game state or balances.
- [ ] Test Firestore contention, quotas, cost, recovery, and load before claiming multi-worker or 1,000-10,000-player capacity.

## Security, Economy, and Operations

- [ ] Define Firestore wallet accounts for `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, `CLUB_AGENT`, `CLUB_PLAYER`, and table escrow, with explicit owners, scopes, and currency types.
- [ ] Support one Firebase identity as both an `AGENT` and a `PLAYER` with separate club-scoped agent-operating and personal-player wallets/ledgers. Define role grants, transfer boundaries, idempotency, and audit rules; never merge these balances.
- [x] On new account creation, provision a persistent `GLOBAL_PLAYMONEY` wallet and grant exactly 20,000 play-money chips once through trusted server-side provisioning. The implementation uses a reserve-backed debit and records accounting rows; never expose an arbitrary client-controlled grant amount or mix it with club accounts.
- [x] Confirm the club treasury grant path as a reserve-backed 10,000-chip initial grant that creates club-owned treasury state without creating an owner personal balance. Preserve distinct account scopes and audit records.
- [ ] Specify double-entry wallet movements for treasury-to-agent funding, agent-to-player distribution/claim-back, player buy-in to table escrow, hand settlement, and cash-out. Commit ledger entries and cached account balances atomically and idempotently.
- [ ] Add a player-authorized `GET` wallet summary/history API with explicit available/in-play values and strict separation of `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, operator, player, and table-escrow balances.
- [ ] Define per-table lifetime semantics: every table has an assigned scheduled close datetime; its lifetime may vary (for example, 6 or 24 hours) based on that assignment. The weekly club settlement cutoff is an additional close boundary, not the only expiration schedule.
- [ ] Choose the club timezone and exact boundary behavior; store the assigned deadline as an unambiguous `scheduled_close_at`, separate from `closed_at` (the actual time the table was closed), and expose/configure it through the appropriate table contract.
- [ ] Implement an idempotent expiration worker that stops new hands, applies a policy for hands still in progress, settles or explicitly carries each seated stack, releases seats safely, and preserves hand/ledger audit history. Do not delete live seats or reset balances without accounting for in-play chips.
- [ ] Test cutoff boundaries, delayed/retried expiration jobs, active-hand handling, and recovery when the service is unavailable at the cutoff.
- [ ] Validate platform club owner UIDs and add request idempotency to prevent duplicate initial grants.
- [ ] Review authorization for every REST route, WebSocket action, table, union, and administrative operation.
- [ ] Add an idempotent player cash-out request/approval API and define/test seat exit, rebuy/top-up, external payout/refund, and reconciliation before treating club balances as a complete money ledger.
- [ ] Add Firestore schema versions, access rules/indexes, emulator-backed transaction tests, and backup/restore drills.
- [ ] Add per-table metrics (mailbox depth, action latency, timer drift, lease renewals), alerts, rate limits, and deployment/rollback checks.

## Later Game Scope

- [ ] Add PLO/PLO5/PLO6 as validated configurations only after the NLH engine is stable; preserve Omaha's exact-two-hole/exact-three-board rule.
- [ ] Build Tongits as a separate engine with its own state, actions, meld evaluation, and scoring.
- [ ] Add tournament registration, an authoritative tournament clock, blind-level commands, eliminations, table balancing, prize settlement, and recovery as a separate domain service.
- [ ] Keep unverified games unavailable even if the game catalog lists them.
