# Project TODO

Last reviewed: 2026-10-19

This list tracks repository work, not just architecture proposals. A completed diagram or document does not mean its runtime components are implemented.

## Next Work: Durable Multi-Worker Tables

Complete these in order before running live tables across multiple backend instances:

- [x] Require `WORKER_COORDINATION_TOKEN` bearer authentication on `/workers/{worker_id}/...`, fail closed when unset, reject HTTP force-reclaim requests, and reject mismatched path/payload worker IDs and unstarted sessions.
- [ ] Restrict worker-route ingress to trusted services and manage/rotate the token through the deployment secret manager.
- [x] Connect `TableRuntime` lease claim/renew/authorize/release methods and club/union per-table claim routes to Firestore lease operations when Firestore is enabled.
- [x] Replace the Firestore-enabled `/workers/{worker_id}/...` process-local coordinator path with scope-aware durable sessions, assignment discovery, claim, heartbeat, release, and recovery; retain the in-memory coordinator only when Firestore is disabled.
- [x] Persist initial table checkpoints and restore table state, owner, and fencing generation when a worker claims a table in a new process.
- [ ] Finish lease-fenced checkpointing for remaining table settings/lifecycle mutations; club/union buy-ins, hand starts, player actions, pre-actions, and timeout actions are already transactionally checkpointed when Firestore is enabled.
- [x] Add opt-in Pub/Sub/Redis routing for WebSocket player/pre-actions to the lease owner and ordered, recipient-masked snapshot fanout to active gateway instances.
- [ ] Route REST table mutations to the current owner and complete owner-state discovery across instances; WebSocket player/pre-actions route through opt-in Pub/Sub, while REST and settings/lifecycle operations do not.
- [ ] Prove concurrent claims, stale-worker rejection, retries, and crash/restart recovery with Firestore-emulator tests; then load-test 100 concurrent tables and measure contention, latency, quotas, and cost before increasing worker count.

## Completed in the Repository

- [x] Implement separate currency-scoped reserves for global play-money grants and 10,000-chip club-creation giveaways, with atomic debit, destination credit, and ledger logging.
- [x] Add self-service `POST /clubs`: derive the owner from the Firebase UID and grant 10,000 `CLUB_CHIPS` from the pre-provisioned giveaway reserve.
- [x] Add club member role administration: owner/manager-only active member directory and audited promotion/demotion transitions; protect owner and block self-role changes.
- [x] Provision the production `CLUB_CHIPS` giveaway reserve with a 1,000,000,000-chip opening allocation and audit record.
- [x] Refresh the Firebase architecture diagram statuses for the current single-process runtime, implemented checkpoint primitive, and separate global/club-chip reserves; keep unfinished scaling and recovery components marked as targets.
- [x] Add reserve-backed accounting metadata (`from_wallet_id`, `to_wallet_id`, `amount`, `currency_type`, and timestamps) to initial funding records; operations with stable transaction IDs are retry-idempotent.
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
- [x] Add versioned JSON checkpoint codecs for NLH hand/deck state and the enclosing table state, including seats, settings, button rotation, pre-actions, replay frames, engine events, and pending completed hands.
- [x] Add a Firestore transaction primitive that initializes versioned table checkpoints and atomically advances a checkpoint with a user/action-ID journal record, guarded by expected sequence and fencing epoch.
- [x] Verify Firestore settlement against the emulator locally (2026-10-19: 1 passed with JDK 21); repeat as a release gate before production rollout.

## UI Client Work

The client implementation checklist lives in [UI_GUIDE.md](UI_GUIDE.md); API request/response contracts live in [API.md](API.md). The frontend source is maintained in the separate client repository.

## Before Multi-Worker Live Tables

- [x] Add a Firestore transaction primitive that atomically advances a table checkpoint and records the action fingerprint, guarded by expected sequence and fencing epoch.
- [x] Validate matching lease owner, fencing generation, and unexpired lease in the same Firestore transaction as a table action checkpoint commit.
- [x] Commit first-party hand starts and explicit player actions through the fenced checkpoint/action transaction when Firestore is enabled.
- [x] Commit standalone pre-action changes through the fenced checkpoint/action transaction before acknowledging them.
- [x] Commit timeout-driven folds/checks and resulting pre-action effects through the fenced checkpoint/action transaction.
- [x] Atomically couple club and union buy-in balance/seat/ledger writes with the lease-checked table checkpoint and action journal.
- [x] Initialize durable generation-zero table checkpoints during club/union table creation; create union metadata and checkpoint atomically.
- [x] Add Firestore transaction-backed table lease repository operations for claim, renewal, authorization, and release; preserve fencing generations across expiry and release.
- [x] Wire durable leases into runtime ownership, scoped worker claim/heartbeat/release routes, and checkpoint recovery; Firestore-backed ownership no longer relies on process-local leases.
- [ ] Check lease owner, fencing generation, and expiry in the same Firestore transaction that commits each authoritative table mutation; a separate pre-write authorization check is not sufficient.
- [ ] Add protected/encrypted checkpoint storage and turn-deadline recovery.
- [x] Persist Firestore-backed player-action fingerprints in the table action journal for implemented mutation paths.
- [ ] Add reconnect replay cursors and complete replay/recovery semantics across worker deliveries.
- [ ] Keep current live-table testing on one backend instance until REST routing, full recovery, and cross-worker validation exist.
- [x] Define the opt-in Pub/Sub command/reply/event transport and ordering; Pub/Sub remains backend-internal and does not own game state or balances.
- [ ] Deploy the gateway/runtime split with Redis presence, least-privilege IAM, and one stable worker ID per single-instance Cloud Run worker service; complete REST mutation routing before enabling live tables.
- [ ] Test Firestore contention, quotas, cost, recovery, and load before claiming multi-worker or 1,000-10,000-player capacity.

## Security, Economy, and Operations

- [ ] Define Firestore wallet accounts for `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, `CLUB_AGENT`, `CLUB_PLAYER`, and table escrow, with explicit owners, scopes, and currency types.
- [ ] Support one Firebase identity as both an `AGENT` and a `PLAYER` with separate club-scoped agent-operating and personal-player wallets/ledgers. Define role grants, transfer boundaries, idempotency, and audit rules; never merge these balances.
- [x] On new account creation, provision a persistent `GLOBAL_PLAYMONEY` wallet and grant exactly 20,000 play-money chips once through trusted server-side provisioning. The implementation uses a reserve-backed debit and records accounting rows; never expose an arbitrary client-controlled grant amount or mix it with club accounts.
- [x] Confirm the club treasury grant path as a reserve-backed 10,000-chip initial grant that creates club-owned treasury state without creating an owner personal balance. Preserve distinct account scopes and audit records.
- [ ] Specify double-entry wallet movements for treasury-to-agent funding, agent-to-player distribution/claim-back, player buy-in to table escrow, hand settlement, and seat exit returning the final table stack to the member's available chip balance. Commit ledger entries and cached account balances atomically and idempotently.
- [x] Record local-club NLH rake attribution and player results per hand, with cumulative club-scoped player rake/winnings summaries written idempotently with hand settlement. Rake is allocated once per hand among eligible players by contestable contribution; preflop folders are excluded, later folders retain eligibility. Expose owner/manager club totals and assigned-agent downline reports; deny players access. Keep these records separate from player wallet and in-play balances. Union rake allocation remains undefined and is not included. See [rakeRules.md](rakeRules.md) for the implemented rule.
- [ ] Define and implement rakeback plus agent/manager/owner share accrual and distribution; confirm the percentages before enabling payouts. Proposed example: 1,000 chips of rake attributed 50/30/20 to three players; at 40% rakeback, accrue 20/12/8 to those players, then split the remaining 60 as agent 30, manager 10, owner 20. Do not mix accrued shares with available or in-play balances.
- [ ] Add a player-authorized `GET` wallet summary/history API with explicit available/in-play values and strict separation of `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, operator, player, and table-escrow balances.
- [ ] Replace timestamp-only rake hand-history pagination with a stable composite `(timestamp, hand_id)` cursor; preserve the cursor ordering in Firestore queries and API responses.
- [ ] Define per-table lifetime semantics: every table has an assigned scheduled close datetime; its lifetime may vary (for example, 6 or 24 hours) based on that assignment. The weekly club settlement cutoff is an additional close boundary, not the only expiration schedule.
- [ ] Choose the club timezone and exact boundary behavior; store the assigned deadline as an unambiguous `scheduled_close_at`, separate from `closed_at` (the actual time the table was closed), and expose/configure it through the appropriate table contract.
- [ ] Implement an idempotent expiration worker that stops new hands, applies a policy for hands still in progress, settles or explicitly carries each seated stack, releases seats safely, and preserves hand/ledger audit history. Do not delete live seats or reset balances without accounting for in-play chips.
- [ ] Test cutoff boundaries, delayed/retried expiration jobs, active-hand handling, and recovery when the service is unavailable at the cutoff.
- [ ] Validate platform club owner UIDs and add request idempotency to prevent duplicate initial grants.
- [ ] Review authorization for every REST route, WebSocket action, table, union, and administrative operation.
- [ ] Restrict Cloud Run ingress and manage/rotate `WORKER_COORDINATION_TOKEN` through the deployment secret manager; the application bearer-token gate is implemented.
- [ ] Implement idempotent seat exit/stack return from in-play chips to the member's available chip balance, then release the seat. Authorized managers and assigned agents can claim available member chips through the existing `/wallet/claim-back` route; the real-world cash/bank transfer is handled manually outside the app, with no in-app fiat payout API or payment record planned.
- [ ] Add Firestore schema versions, verify deployed rules deny direct client reads/writes to hand audits and `player_rake_stats`, configure required indexes, add emulator-backed transaction/rule tests, and run backup/restore drills.
- [ ] Add per-table metrics (mailbox depth, action latency, timer drift, lease renewals), alerts, rate limits, and deployment/rollback checks; see [MONITORING_TODO.md](MONITORING_TODO.md) for the staged monitoring plan.

## Later Game Scope

- [ ] Next game-engine sequence: validate PLO4 first, then PLO5 and PLO6 as separate configurations; preserve Omaha's exact-two-hole/exact-three-board rule and keep each variant unavailable until verified.
- [ ] Build Tongits as a separate engine with its own state, actions, meld evaluation, and scoring.
- [ ] Deferred until after PLO4/PLO5/PLO6: add tournament registration, an authoritative tournament clock, blind-level commands, eliminations, table balancing, prize settlement, and recovery as a separate domain service.
- [ ] Keep unverified games unavailable even if the game catalog lists them.
