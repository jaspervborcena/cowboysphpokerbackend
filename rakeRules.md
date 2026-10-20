# Rake Rules: Implemented Behavior

Status: implemented on `feature/rakeImplementation` as of 2026-10-20. This document describes current code behavior for local club No-Limit Texas Hold'em. It is intended for peer review; it is not a rakeback or payout policy.

## Scope

- Rake is calculated and attributed once per completed hand.
- The amount collected follows the table's rake settings.
- The whole-hand rake is attributed among rake-eligible players in proportion to their contestable contributions.
- A player who folds preflop is ineligible. Folding on the flop, turn, or river does not revoke eligibility.
- This is not a separate rake calculation or bucket for each street.
- Attribution is private club accounting. It does not credit a player's wallet or in-play balance.
- Union-hand rake allocation, rakeback, and agent/manager/owner revenue shares are not implemented.

## Rake Collection

For a completed hand, the engine uses the contestable pot total and the table settings:

1. Rake is eligible when the configured rate is greater than zero and the pot meets `rake_min_pot`.
2. With `no_flop_no_drop` enabled, at least a flop must have been dealt. This setting defaults to enabled.
3. The raw rake is the pot multiplied by `rake_rate`, rounded down to a whole chip.
4. If `rake_cap` is greater than zero, collected rake is the lower of raw rake and the cap.
5. Otherwise collected rake is the raw rake. The table API accepts a `rake_rate` up to 10%.

In shorthand:

```text
if rake_rate <= 0 or pot_total < rake_min_pot:
    rake_amount = 0
elif no_flop_no_drop and no flop was dealt:
    rake_amount = 0
else:
    rake_amount = floor(pot_total * rake_rate)
    if rake_cap > 0:
        rake_amount = min(rake_amount, rake_cap)
```

Normal table rake and promotion drops are separate. The current `jackpot_fee` setting is applied after normal rake, based on the remaining pot, and is added to `bad_beat_jackpot_pool`; it is not included in `rake_attributed` or `total_rake_generated`. The proposed fixed integer drops for separate BBJ, Jackpot, and High Hand pools in [jackpot.md](jackpot.md) are not implemented yet.

## Player Eligibility

The hand audit's action frames identify a fold made while the hand was preflop. A player with such a fold is marked `rake_eligible: false` and receives zero rake attribution. All other dealt-in players are marked eligible, including a player who later folds or is all-in. An eligible player with no contestable contribution receives a zero share.

If a flop is dealt and a player folds on the flop, turn, or river, their eligibility remains true. Their contribution to the contestable pot remains part of the attribution calculation even though they no longer continue in the hand.

Matched blind/ante or other contestable contributions from a player who folds preflop remain in the contestable pot used to calculate the hand's rake, but the player is excluded from the attribution denominator. The whole collected rake is attributed proportionally among eligible players. Uncalled chips are returned and removed from the contestable pot before rake and attribution are calculated; they are not dead money.

With the default no-flop-no-drop setting, a hand ending preflop collects no rake. If the table explicitly disables that setting, a preflop-only hand can collect rake; preflop folders remain excluded and non-folded players remain eligible.

## Attribution Formula

First remove any uncalled return from each player's total contribution:

```text
contestable_contribution[player] = total_contribution[player] - uncalled_return[player]
```

Then include only eligible players in the denominator. Each eligible player's exact proportional share is:

```text
exact_share[player] = rake_amount
                      * contestable_contribution[player]
                      / sum(eligible contestable contributions)
```

Shares are integer chips. The implementation takes the whole-chip floor for each exact share, then assigns any remaining chips by largest fractional remainder. If fractional remainders tie, table seat order breaks the tie deterministically. Ineligible players receive zero. The final per-player shares always sum exactly to the collected rake.

### Example

Assume a hand has a 6,000-chip contestable pot, the table collects 600 chips of rake, and four players contributed 1,500 chips each. Player A folded preflop; Players B, C, and D did not fold preflop. Each of B, C, and D has a 1,500-chip eligible contribution, so:

| Player | Eligibility | Contestable contribution used | Rake attributed |
| --- | --- | ---: | ---: |
| A | No, folded preflop | Excluded | 0 |
| B | Yes | 1,500 | 200 |
| C | Yes | 1,500 | 200 |
| D | Yes | 1,500 | 200 |
| Total | | 4,500 eligible | 600 |

If C folds on the turn instead, C remains eligible and retains the rake attribution generated from this hand. This example is a whole-hand split: it does not allocate 600 only to players who called a particular flop bet.

## Per-Hand Records

The private hand audit at `clubs/{club_id}/tables/{table_id}/hands/{hand_id}` records these per-player values:

- `rake_eligible`: whether the player avoided folding preflop.
- `contestable_contribution`: total contribution less uncalled returns.
- `uncalled_return`: chips returned because no opponent matched the contribution.
- `gross_award`: gross pot award before rake and other fees.
- `rake_deduction`: this winner's integer-chip share of the hand's collected rake.
- `bbj_drop_deduction`: this winner's integer-chip share of the BBJ drop.
- `payout_after_fees`: amount added to the player's stack after fees/drops.
- `rake_attributed`: the player's share of collected rake, not a payment.
- `net_delta`: `final_stack - starting_stack`, the player's actual stack change for the hand.

The hand summary separately records hand-level `rake_amount` and `jackpot.fee_collected`. Winner rake deductions are allocated across gross awards first; the BBJ drop is then allocated across the remaining awards. Each allocation uses largest-remainder whole-chip rounding with the table hand order as its deterministic tie-break. For each current record, `payout_after_fees = gross_award - rake_deduction - bbj_drop_deduction`. `rake_attributed` is generated-rake reporting, not a fee deducted from this player's award. Gross award, post-fee payout, and net result are different measures. Do not describe all three as simply "winnings." `rake_attributed` also does not indicate that rakeback was paid. Older records may not contain the new fee-deduction fields.

## Cumulative Club Records

Each settled local club hand creates one record per participant, including participants with zero rake:

```text
clubs/{club_id}/player_rake_stats/{user_id}/hands/{hand_id}
clubs/{club_id}/player_rake_stats/{user_id}
```

The per-hand record preserves that player's contribution, eligibility, rake attribution, awards, net result, table, and timestamp. The parent player document maintains cumulative counts and totals such as `hands_played`, `rake_eligible_hands`, `hands_with_award`, `positive_net_hands`, `total_contestable_contribution`, `total_gross_awards`, `total_payout_after_fees`, `total_net_result`, and `total_rake_generated`.

The transaction that settles the hand updates the member's in-play balance, seat stacks, club rake pool, per-hand records, and player summary documents together. A hand cannot be counted twice on retry: the existing hand's pending-to-settled status and unique hand IDs guard the transaction. Pending pre-feature hand audits are recovered using their saved player fields and action frames when available. Historical club rake that has no player attribution remains `unattributed_rake_total`; recovery does not invent player shares.

## Reports and Visibility

- `GET /clubs/{club_id}/rake/players`: owners/managers see the club total, attributed/unattributed totals, and paginated player summaries sorted by rake generated then user ID. Owner totals use a Firestore aggregate query. Agents see only active assigned players and their rake-generated total; their response does not include club-wide rake totals. Agent stats are batch-read for the current downline to preserve exact scope totals and ranking; page-scoped agent reads remain follow-up work.
- `GET /clubs/{club_id}/rake/players/{user_id}/hands`: owners/managers can inspect club member hand records; an agent can inspect only a currently active player assigned to that agent. Pagination uses the composite descending `(timestamp, hand_id)` cursor; both `before_timestamp` and `before_hand_id` must be supplied together. See [API.md](API.md) for the response fields.
- Players receive `403` for both report routes. Player wallet, public snapshots, and replay responses do not expose rake attribution.
- These routes require an authenticated active club membership. The backend enforces report permissions; deployed Firestore rules must also deny direct client reads/writes to `player_rake_stats` and private hand audits before direct client access is permitted.

## Settlement and Balance Boundary

Rake attribution is a report/statistic. It does not pay a player, agent, manager, or owner. It does not alter `chip_balance` or `in_play_balance`. Collected rake is separately added to the club's `treasury/pools.total_rake_collected` and recorded in the club transaction ledger by the hand settlement transaction. Winner rake deductions are allocated over gross awards first; the jackpot fee is then allocated over the remaining awards. These fee deductions are separate from generated-rake attribution.

## Not Implemented

- Street-by-street rake buckets. Current rake is collected once per hand and distributed once across eligible players.
- Promotion-specific fixed integer drops split among BBJ, Single Jackpot, and High Hand pools; current `jackpot_fee` only updates the BBJ pool.
- Player rakeback balances or payments.
- Agent, manager, or owner commission/profit shares.
- A confirmed percentage policy for rakeback or role shares.
- Union rake collection or per-home-club allocation for union hands.
- Direct player access to rake reports.
- Deployed Firestore rules in this repository; direct-access denial must be verified in the target Firebase project.

The existing proposal in `TODO.md` uses 40% rakeback and splits the remaining 60% among operator roles only as an example. It is not an implemented or approved distribution policy.
