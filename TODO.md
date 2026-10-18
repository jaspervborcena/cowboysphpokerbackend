# Project TODO

Last reviewed: 2026-10-18

This list tracks repository work, not just architecture proposals. A completed diagram or document does not mean its runtime components are implemented.

## Next Work: Durable Multi-Worker Tables

Complete these in order before running live tables across multiple backend instances:

- [x] Require `WORKER_COORDINATION_TOKEN` bearer authentication on `/workers/{worker_id}/...`, fail closed when unset, reject HTTP force-reclaim requests, and reject mismatched path/payload worker IDs and unstarted sessions.
- [x] Hide worker coordination routes from gateway-role instances and support a separate coordination-token header for Cloud Run IAM callers.
- [x] Connect `TableRuntime` lease claim/renew/authorize/release methods and club/union per-table claim routes to Firestore lease operations when Firestore is enabled.
- [x] Replace the Firestore-enabled `/workers/{worker_id}/...` process-local coordinator path with scope-aware durable sessions, assignment discovery, claim, heartbeat, release, and recovery; retain the in-memory coordinator only when Firestore is disabled.
- [x] Persist initial table checkpoints and restore table state, owner, and fencing generation when a worker claims a table in a new process.
- [ ] Finish lease-fenced checkpointing for remaining table settings/lifecycle mutations; club/union buy-ins, hand starts, player actions, pre-actions, and timeout actions are already transactionally checkpointed when Firestore is enabled.
- [x] Finish lease-fenced checkpointing for table settings/lifecycle mutations; club/union buy-ins, hand starts, player actions, pre-actions, timeout actions, settings, and union-table closure use Firestore transaction fencing when enabled.
- [x] Commit club and union table settings updates through the lease/fencing/sequence-checked checkpoint transaction, keyed by a stable `settings_update_id`.
- [ ] Fence remaining table lifecycle mutations in the checkpoint transaction; buy-ins, hand starts, player actions, pre-actions, timeout actions, and settings are already covered when Firestore is enabled.
- [x] Add opt-in Pub/Sub/Redis routing for WebSocket player/pre-actions to the lease owner and ordered, recipient-masked snapshot fanout to active gateway instances.
- [ ] Route REST table mutations to the current owner and complete owner-state discovery across instances; WebSocket player/pre-actions route through opt-in Pub/Sub, while REST and settings/lifecycle operations do not.
- [ ] Prove concurrent claims, stale-worker rejection, retries, and crash/restart recovery with Firestore-emulator tests; then load-test 100 concurrent tables and measure contention, latency, quotas, and cost before increasing worker count.
- [ ] Deploy the gateway/runtime split only after the code and validation gates above pass: use internal Cloud Run ingress for worker services, IAM invoker allowlisting, a worker-only Secret Manager token, unique worker IDs, and one instance per worker service.

## Completed in the Repository

- [x] Add unique union-letter creation and lookup, founder admin/representative setup, representative approval, and separate club membership request/approval APIs.
- [x] Create union-linked tables through the origin club route with union theme/code metadata; restrict table settings/start/delete to origin-club owners/managers and share discovery only with active member clubs.
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

- [ ] Implement server-authoritative membership and moderation lifecycle APIs: manager may demote agents to players and kick players; owners may kick managers; union leaders may suspend/remove union clubs and close clubs after a grace period. Enforce hand-safe kicks, deny play and manager/agent claim-backs while suspended, preserve records on closure, and audit actor/reason/time.
- [ ] Define club plans with 1-10 star levels and plan-specific member limits (including 5,000-member and unlimited tiers); enforce capacity on admission and require at least 3 stars before linking a club to a union. Define plan-downgrade behavior when current membership exceeds the new limit.
- [ ] Separate club identifiers: keep `clubs/{club_uuid}` UUID-keyed, allocate and return a collision-safe 8-digit business `club_id`, and use `club_uuid` in operational REST/local-WebSocket paths. Add a flat `club_id_index/{club_id}` mapping for public search, invitations, and union-socket home-club resolution. Test uniqueness, lookup, invalid IDs, and authorization; do not add `/v2` routes.
- [ ] Define and implement `users/{user_id}` as the Firebase-UID-keyed profile with a server-assigned unique 8-digit `player_id`; add a collision-safe player-ID lookup/index for invitation resolution, expose both IDs through authenticated APIs, and enforce profile ownership without treating IDs as credentials.
- [ ] Add club invitation/join APIs that target members by 8-digit `player_id`, derive the joining `user_id` from Firebase auth, and accept an optional 8-digit `agent_player_id` for referral/agent assignment. Validate the agent's club role and authorization server-side; migrate current UID-valued `agent_id` references without trusting client-provided UIDs.
- [ ] Define and implement same-location enforcement for ring games: when a location match is confirmed between an ordinary player and a VIP, remove the ordinary player. Keep VIP priority separate from authorization roles; define location threshold/accuracy, equal-priority handling, consent/privacy/retention, hand-boundary behavior, and server-side verification before enforcement.
- [ ] Model club-scoped `agent_groups/{agent_group_id}` with both `club_uuid` and `club_id`, original `agent_player_id`, current acting-agent ID, operational `agent_balance`, and separate `rakeback_balance`; store each assigned player in a separate flat membership document. Update balances only through server-side ledger transactions and preserve group membership/rakeback across manager takeover and restoration.
- [ ] Define Firestore wallet accounts for `DIAMONDS`, `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, `CLUB_AGENT`, `CLUB_PLAYER`, and table escrow, with explicit owners, scopes, and currency types.
- [ ] Keep new Firestore business records flat and small, using top-level documents with reference IDs instead of embedded child objects, arrays of ledger/member records, or deep paths. Respect Firestore's fixed 1 MiB document limit; existing nested paths require an explicit migration rather than an implicit rename.
- [ ] Add a separate `DIAMONDS` currency for player and club accounts, with server-verified card/GCash purchases, idempotent payment and ledger records, and explicit rules for player/club transfers, refunds, and chargebacks.
- [ ] Define a server-owned Diamond catalog and atomic spending flows for VIP/Black VIP, avatars, card protection, emojis, table themes, club creation/plan upgrades, club chips (example 200 Diamonds for 100,000 chips), and additional global play-money chips; define item duration, pricing, and permitted conversions without merging currency balances.
- [ ] Add player inventory/entitlement records and purchase/transfer APIs for Diamonds; never grant currency or paid items from client-provided balances or unverified payment callbacks.
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
- [ ] Standardize Firestore ownership/audit fields on new and migrated business records: Firebase UID `user_id` where user-owned, server timestamps `created_at`/`updated_at`, and actor UIDs `created_by`/`updated_by`. Keep immutable ledger/audit records creation-only; migrate existing records before rules require these fields.
- [ ] Implement a shared trace/event envelope with `trace_id`, request/action/transaction IDs, actor/target and scope IDs, event type, outcome, source service, and server timestamp. Correlate REST, WebSocket, Pub/Sub, and worker operations; write structured events to Google Cloud Logging and export selected events asynchronously to partitioned/clustered BigQuery tables. Do not write generic logs to Firestore; keep authoritative financial ledger records in Firestore transactions. Define redaction, retention, access controls, cost limits, and tests.
- [ ] Add an idempotent player cash-out request/approval API and define/test seat exit, rebuy/top-up, external payout/refund, and reconciliation before treating club balances as a complete money ledger.
- [ ] Add Firestore schema versions, access rules/indexes, emulator-backed transaction tests, and backup/restore drills.
- [ ] Add per-table metrics (mailbox depth, action latency, timer drift, lease renewals), alerts, rate limits, and deployment/rollback checks.

## Next Game Scope After NLH

- [x] NLH is the implemented and create-supported baseline.
- [ ] Validate PLO4, PLO5, and PLO6 as separate configurations; preserve Omaha's exact-two-hole/exact-three-board rule and keep each variant unavailable until verified.
- [ ] Build Tongits as a separate engine with its own state, actions, meld evaluation, and scoring.
- [ ] After PLO4, PLO5, PLO6, and Tongits: add tournament registration, an authoritative tournament clock, blind-level commands, eliminations, table balancing, prize settlement, and recovery as a separate domain service.
- [ ] Keep unverified games unavailable even if the game catalog lists them.
