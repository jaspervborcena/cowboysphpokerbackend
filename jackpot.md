# Jackpot and Promotion Rules Proposal

Status: BBJ v1 and a 3-hour Hand-of-the-Day high-hand promotion are implemented for supported local club NLH. The remaining sections describe future Single-Hand Jackpot and promotion variants. PLO4/5/6 are not currently playable.

## Goals

- Keep promotion eligibility separate from prize calculation and accounting.
- Keep BBJ, jackpot, and scheduled high-hand promotion accounting separate.
- Make every contribution and award auditable, idempotent, and tied to a completed hand or promotion period.
- Make game and stake rules data-configurable without allowing an LLM or client to decide awards.
- Keep all values in integer `CLUB_CHIPS`; this proposal does not define real-money payments.

## Current Implementation

New local club NLH tables default to `jackpot_enabled: true` with a 3% BBJ contribution from the contestable pot, in addition to the 7% default rake. The contribution is rounded down to an integer chip and collected after rake, capped by the remaining pot. The legacy `jackpot_fee` remains available as a fixed-chip contribution; if set without an explicit `jackpot_rate`, it replaces the default 3% rate. Owners may make optional contributions from their member `available_balance`. The `jackpot_min_pot` setting applies to the pot remaining after rake. The configured natural-language `jackpot_qualifying_hand` remains descriptive only; it is not evaluated as a rule.

BBJ v1 qualification uses rule version `nlh-bbj-aces-full-v1`: a showdown must include at least four dealt-in players and a contestable pot of at least 20 big blinds. The losing player's normal best-five Hold'em hand must be Aces Full or better, and the highest winning hand must be Four of a Kind or better. Tied highest hands are all winning qualifiers; every lower qualifying hand is a losing qualifier. Best-five evidence records the exact hole and board cards used. The existing first-party Hold'em evaluator is used; no extra hole-card/board-card composition restriction applies.

The club BBJ pool is shared by its local NLH tables. Pool access is a direct percentage of the pool after the current hand's drop: 1% at a 2-chip big blind, 5% at 50, 10% at 100, and 15% at 200 and above, with linear interpolation by big blind between those points. The access rate is capped at 15%; the approved reserve floor is zero. Integer award allocation uses largest remainders for the 50% losing-hand / 25% winning-hand / 25% table portions. Each portion is then divided equally among its recipients, with odd chips assigned by seat order. Table-share recipients are other dealt-in, rake-eligible players, excluding every qualifier. All three portions credit those players' member in-play balances (`in_play_balance`), not their available `available_balance`. If no table-share recipient is eligible, that portion stays in the BBJ pool.

The pool is stored at `clubs/{club_id}/treasury/pools.bad_beat_jackpot_pool` and initialized with each new club. Per-hand contributions, owner-funded contributions, awards, and immutable ledger entries are transactionally recorded. Qualification evidence, the pool calculation, credits, the pool update, and the immutable hand-keyed `BBJ_AWARD` transaction are committed atomically with settlement. Retried hand settlements cannot pay twice. Award records and qualifying card evidence remain in server-only hand/transaction documents; no public BBJ report or player announcement is implemented. Union tables do not collect BBJ drops or pay awards because union-wide pool funding and allocation are undefined. Single-Hand Jackpot and Short Deck remain unimplemented.

### Hand-of-the-Day

The implemented NLH high-hand rules, including blind-level cycle identity, qualification, prize funding, tie sharing, and claims, are documented in [highhandRules.md](highhandRules.md). The prize is 100 big blinds; separate small/big-blind pairs run independent 3-hour cycles and share the club's dedicated Hand-of-the-Day pool.

## Promotion Types and Pool Separation

Proposed promotion types:

| Promotion | Qualification shape | Funding/prize model |
| --- | --- | --- |
| Bad Beat Jackpot (BBJ) | A qualifying losing hand is beaten by a qualifying winning hand at showdown. | Progressive pool, funded by an additional 3% contestable-pot contribution by default, optional fixed-chip `jackpot_fee`, and optional owner contributions. |
| Jackpot | One qualifying hand is sufficient; no losing hand is required. | Not implemented; a separate pool and qualification policy remain to be defined. |
| High Hand | Best qualifying hand during a defined time period. | Implemented for supported NLH as Hand-of-the-Day; funded from the club treasury into a dedicated shared pool. See [highhandRules.md](highhandRules.md). |

Pools should be separately accounted for per club and promotion in `CLUB_CHIPS`. Decide whether BBJ/Jackpot pools are shared across game variants or segregated by game before launch. A pool balance is an accounting liability/reserve, not a player wallet balance.

### Promotion Drop Funding

The implemented BBJ contribution defaults to 3% of the contestable pot, in addition to the default 7% rake; see [rakeRules.md](rakeRules.md). Optional owner contributions transfer member available club chips into `bad_beat_jackpot_pool`. Hand-of-the-Day is funded separately through owner/manager treasury-to-pool transfers. All pool mutations and ledger records are transactional and idempotent.

### Interaction with Player Rake Attribution

Normal table rake and promotion drops are separate fees. Rake is calculated on the contestable pot, which includes matched blind/ante or other contestable chips contributed by a player who later folds preflop. That dead money can therefore generate rake. The rake-report rule excludes a preflop folder from the attribution denominator, so the rake generated by the whole pot is reattributed proportionally to eligible players. Truly uncalled chips are returned before the contestable pot is calculated; they are not dead money and do not generate rake. See `rakeRules.md` for the implemented rake attribution rule.

## Separation of Eligibility and Payout

For each promotion, evaluate eligibility first and produce a deterministic qualification result. Only then calculate the prize from the configured pool-access policy. The eligibility result should identify the game/config version, qualifying players, hand ranks, cards used, minimum-player/pot checks, and a stable reason code for pass/fail.

Do not use natural-language `qualifying_hand` text as executable rules. Store a validated, versioned rule configuration and implement its evaluator in deterministic server code. AIs may explain reports or help an operator draft configuration, but must not decide a live hand result, award, or balance mutation.

## Candidate Eligibility Rules

These examples need rule-owner approval and exact evaluator tests before they can be enabled.

### Hold'em BBJ

Proposed conditions:

- A showdown occurs with at least four players dealt into the hand.
- Contestable pot is at least 20 big blinds.
- The losing hand is Aces Full or better and loses to Four of a Kind or better.
- Both qualifying hands use exactly two hole cards and three board cards, if that is the intended club rule. Standard Hold'em best-five evaluation does not itself require exactly two hole cards, so this must be an explicit promotion-specific check separate from normal hand ranking.

Example candidate: Aces Full loses to Fives Quads: qualifies. Queens Full loses to Aces Quads: does not qualify because the losing hand is below the configured minimum, if Aces Full is the minimum. Define comparisons using the evaluator's rank values, not labels alone.

The additional recommendation that a Four-of-a-Kind qualifier must hold a pocket pair is a separate stricter condition; it does **not** follow automatically from requiring exactly two hole cards in the best five. Decide and test that requirement independently. The promotion evaluator should record which exact two hole cards and three board cards formed each qualifying hand.

### PLO4 BBJ

Candidate: a losing Straight Flush is beaten by a higher Straight Flush or Royal Flush. PLO evaluation must use exactly two hole cards and exactly three board cards. Define whether every Straight Flush qualifies as the losing hand or only a minimum high card qualifies.

### PLO5 BBJ

Candidate: a losing Jack-high-or-better Straight Flush is beaten by a higher Straight Flush or Royal Flush. This wording needs an exact minimum rank and a clear rule for Royal Flush hands before implementation.

### Single-Hand Jackpot

This is distinct from BBJ because it has no losing-hand requirement. Candidate qualification examples:

| Game | Candidate qualifying hand |
| --- | --- |
| Hold'em | Four of a Kind, Straight Flush, or Royal Flush |
| PLO4 | Royal Flush |
| PLO5 | Royal Flush |
| Short Deck | Straight Flush |

These are examples only. Each game needs an explicit evaluator, hand-construction rule, and test fixtures before the game may participate.

## Stake Tiers and Pool Access

A fixed percentage of a large pool can make small-stake rewards too high and high-stake rewards too low. Use a versioned Promotion Matrix keyed by promotion, game, and stake tier. A row should specify the effective pool-access percentage and payout split for the qualifying class.

Candidate stake coefficients (examples only):

| Blinds | Coefficient |
| --- | ---: |
| 1/2 | 1.0x |
| 2/4 | 1.2x |
| 5/10 | 1.5x |
| 10/20 | 2.0x |
| 25/50 | 4.0x |
| 50/100 | 8.0x |
| 100/200 | 15.0x |
| 200/400 | 30.0x |

Candidate pool-access examples: 1/2 accesses 1% of the current pool; 25/50 accesses 5%; 50/100 accesses 10%; 100/200 accesses 15%. A matrix can directly store the effective access percentage for each promotion/game/stake row. If coefficients and game modifiers are retained instead, define exactly how they produce that effective percentage; do not multiply a matrix access percentage by the same modifiers a second time.

Recommended pool safety constraints, pending approval:

```text
effective_access_percent = min(matrix_access_percent, 0.15)
available_for_award = max(pool_balance - reseed_reserve_floor, 0)
candidate_award = floor(pool_balance * effective_access_percent)
award = min(candidate_award, available_for_award)
```

If `available_for_award` is zero, the award is zero. Validate matrix access as non-negative and cap it at 15% even if a configuration row is higher. Store the pre-award pool, configured and effective access percentage, reserve floor, and resulting award in the audit.

The capped formula above supersedes the simpler `min(candidate_award, pool_balance)` calculation because it preserves the configured reseed reserve. Then split `award` using the configured integer payout percentages. The split must sum to 100%, or the remainder must have an explicit destination. If multiple losers, winners, or tables qualify, specify whether they share the applicable portion equally or by another fixed rule. Do not let a multiplier create an award larger than the available pool or consume the reseed reserve.

The alternative formula `pool * game_modifier * stake_modifier` is viable only if the resulting effective access rate is bounded and validated, then clamped by the 15% maximum and reserve floor above. Prefer one unambiguous source of truth: either a direct matrix percentage or a documented modifier calculation.

### Example BBJ Distribution

Candidate low-stake split when the configured access is 1%:

- Pool before award: 600,000 chips
- Award: 6,000 chips
- Losing hand/player: 50% = 3,000
- Winning hand/player: 25% = 1,500
- Table share: 25% = 1,500

Candidate high-stake split when the configured access is 10%:

- Pool before award: 600,000 chips
- Award: 60,000 chips
- Losing hand/player: 50% = 30,000
- Winning hand/player: 25% = 15,000
- Table share: 25% = 15,000

The proposal also mentions 30/15/15 at high stakes; that is the same 50/25/25 split of a 60,000 award. Confirm whether all tiers use the same split or have separate matrix values.

### BBJ Table-Share Eligibility

Candidate table-share recipients are players dealt into the hand with `rake_eligible: true`, excluding every BBJ winner and loser. Sitting-out/away players and preflop folders are ineligible. Divide the table-share amount equally among eligible recipients; distribute any indivisible remainder by table seat order.

If no eligible table-share recipients remain, return the unawarded table share to `bad_beat_jackpot_pool` in the same idempotent award transaction. For a multi-winner or multi-loser BBJ, all qualifying winners and losers are excluded from table share. The final winner/loser split among multiple qualifiers remains to be decided before enabling the promotion.

## High-Hand Promotion

High Hand should use a fixed prize schedule rather than a percentage of the progressive BBJ/Jackpot pool. Candidate period and prizes:

- Every 3 hours.
- Low stakes (1/2 through 5/10): 1st 500, 2nd 300, 3rd 200 chips.
- Mid stakes (10/20 through 25/50): 1st 1,000, 2nd 500, 3rd 300 chips.
- High stakes (50/100 and above): 1st 2,000, 2nd 1,000, 3rd 500 chips.

Define the timezone and period boundary, qualifying-hand ranking, eligible tables/games, treatment of ties, whether a player may occupy multiple positions, and funding source before enabling it. Keep High Hand credits and ledger entries separate from BBJ and Jackpot pools.

Candidate leaderboard tie rules:

1. If two submissions have exactly the same complete hand rank, the hand with the earlier server completion timestamp ranks first.
2. A player may occupy only their single highest-ranking position in a period. If another of their hands would take a lower prize position, skip it and award that position to the next eligible player's best hand.
3. If complete hand rank and completion timestamp are identical, the lexicographically lower `hand_id` ranks first. Treat `hand_id` as a stable opaque string and compare consistently in backend and reports.

## Audit, Idempotency, and Privacy

### Fee Order and Per-Winner Audit Math

Current order in the hand adapter is:

1. Calculate `rake_amount` from the contestable pot using table rake settings.
2. Calculate BBJ eligibility/`fee_collected` from the pot remaining after rake; the drop is capped by the remaining pot.
3. Allocate rake over gross winner awards using largest-remainder whole-chip rounding in table hand order, beginning at the button. Allocate the BBJ drop over the post-rake awards using the same rule. Persist each winner's `gross_award`, `rake_deduction`, `bbj_drop_deduction`, and `payout_after_fees`.

The client/API fee display must use the same order: rake first, then BBJ drop. For an auditable promotion settlement, store hand-level `rake_amount` and `bbj_drop`, and per winner store `gross_award`, `rake_deduction`, `bbj_drop_deduction`, and `payout_after_fees`. The two deduction fields are the winner's share of the hand-level fees; do not subtract the whole hand's fee from every winner. Require:

```text
sum(winner.rake_deduction) = hand.rake_amount
sum(winner.bbj_drop_deduction) = hand.bbj_drop
payout_after_fees[player] = gross_award[player]
					  - rake_deduction[player]
					  - bbj_drop_deduction[player]
```

The implemented deterministic whole-chip allocation uses largest remainder and table hand order, beginning at the button, so each hand can be reconstructed exactly. `rake_attributed` is a different measure: it tracks which eligible players generated rake for reports and can include non-winners. It must not be presented as the winner's `rake_deduction`.

Current private hand and rake-history records expose `gross_award`, `rake_deduction`, `bbj_drop_deduction`, and `payout_after_fees`. Historical records written before the fields were added may omit the separate deductions.

A promotion contribution and any award should be represented by immutable ledger/audit entries keyed by a stable hand/promotion/award ID. In one transaction:

1. Verify the hand is settled and the promotion config version used for it.
2. Record promotion contributions and update the correct pool balances.
3. Record qualification evidence and award calculation, including pre-award pool balance, effective access percentage, gross award, split recipients, rounding, and post-award pool balance.
4. Record per-winner rake and BBJ deductions separately from rake-generated attribution; verify the sums reconcile to the hand-level fees.
5. Apply any chip credits only to the intended club/table account and mark the promotion award settled.
6. Make retries idempotent; never pay an award twice.

Promotion records and private hand cards must be server-only. Add Firestore rules tests denying client SDK reads/writes to hand audits and promotion/rake accounting. Owners/managers and any other authorized role should use authenticated backend report routes. Define what the player, winner, and table UI may display without exposing private cards or other players' accounting data.

### Composite Report Cursor

The player rake-hand report uses a composite descending cursor `(timestamp, hand_id)`. It returns both values as `next_before_timestamp` and `next_before_hand_id`; the next query starts strictly after that tuple. The player summary report uses `(total_rake_generated DESC, user_id ASC)` and returns `next_before_total_rake_generated` plus `next_before_user_id`. Use stable composite cursors for future paginated promotion history too.

### Agent Assignment History

Current agent report authorization filters against the member's current `agent_id`; reassignment therefore changes which agent can see the player's entire existing history. The recommended future model is to record the assigned agent ID (and assignment effective time or version) in every per-hand record. Then Agent A can see records earned during A's assignment period, while Agent B sees records from B's period onward. Define how owners/managers see all periods and how inactive/reassigned members appear before implementing historical agent reports.

### Firestore Rules Gate

Before deploying the report API or allowing any client to read promotion data, deploy and test rules denying client access to private rake statistics and hand audits. A scoped example is:

```javascript
match /clubs/{clubId}/player_rake_stats/{document=**} {
	allow read, write: if false;
}
match /clubs/{clubId}/tables/{tableId}/hands/{handId} {
	allow read, write: if false;
}
```

Firestore `allow` grants from overlapping matches are additive, so verify that no broader rule grants access to these paths. Test both reads and writes through the client SDK in the emulator. Trusted backend Admin SDK access remains server-side and is not governed by client rules.

## Remaining Decisions

- What are the High Hand timezone, period boundaries, eligible games, prize funding source, and how equal-ranked hands interact with the one-prize-per-player rule?
- Define separate pool/access/payout rules for Single-Hand Jackpot, PLO, Short Deck, and any cross-game pool sharing.
- Define how BBJ drops and awards are funded and allocated for union tables before enabling them there.
- Approve composite `(timestamp, hand_id)` cursors and historical agent visibility based on assignment-at-hand time.
- Verify deployed Firestore rules deny direct client reads/writes to private hand and promotion records, including no broader overlapping rule grants.
- Which promotion events and player-facing notifications are allowed, and what data is private?

Until those items are approved and implemented, only the BBJ v1 rules above are active; the rest of this document remains a proposal.
