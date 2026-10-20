# High-Hand Rules

Status: implemented for local club No-Limit Texas Hold'em as the Hand-of-the-Day promotion. PLO4/5/6 and union tables are not supported.

## Scope and cycle identity

Each cycle is independent for a club, game variant, and exact small/big-blind pair. The Firestore state document is:

```text
clubs/{club_id}/hand_of_the_day/{game_id}_{small_blind}_{big_blind}
```

The cycle duration is fixed at three hours. It starts when the promotion is enabled with sufficient pool funding, or when a later cycle's prize is reserved. A qualifying hand does not restart or extend the timer. Hands are checked against their server-recorded completion timestamp so delayed settlement cannot move an older hand into a newer cycle.

## Qualification and ranking

- Only completed NLH showdowns with a complete five-card board are evaluated.
- Each non-folded player's best standard five-card hand from their two hole cards and the five community cards is ranked.
- Four of a Kind or better qualifies. A player's best hand below Four of a Kind does not qualify.
- The strongest qualifying rank becomes the cycle leader. Rank comparison includes the hand category and all standard tiebreakers.
- A stronger rank replaces the current leader without changing the deadline. Players with exactly equal best rank are retained together as tied leaders. A later weaker hand cannot replace the leaders.
- Folded hands, non-showdown hands, and hands completed outside the cycle interval do not qualify.
- No minimum pot or rake eligibility threshold applies.

The evaluator emits server-derived best-hand evidence and a versioned rule identifier (`holdem-hotd-quads-or-better-v1`). Clients cannot submit or alter cards, ranks, or leader identities.

## Prize, funding, and claims

The prize is `100 × big_blind` integer club chips. For example, blinds of 1/5 produce a 500-chip prize. Every blind level has its own cycle, while all levels and supported games share the club's `hand_of_the_day_pool` balance at `clubs/{club_id}/treasury/pools`.

Owners and managers may move chips from the club treasury to the Hand-of-the-Day pool through the funding endpoint. Enabling a blind-level promotion reserves its full calculated prize from the pool. An active cycle keeps its original reserved prize if the configured future-cycle prize changes. Pool funding, cycle reservations, claims, member balances, and audit transactions are updated transactionally.

After the three-hour cycle expires, each tied leader may claim their share. The total reserved prize is split into whole chips as evenly as possible; any remainder goes in seat order. A claim credits the winner's member `available_balance`, not `in_play_balance`, and writes a `HAND_OF_THE_DAY_AWARD` ledger transaction. Claims require the cycle ID and blind pair; retries are idempotent. An unclaimed award remains reserved, and the next prize is not reserved until all tied leaders claim.

If no hand qualifies, the reserved prize rolls forward to the next three-hour cycle. If the pool cannot reserve the next configured prize after all claims are complete, the promotion waits in `awaiting_funding` until more chips are contributed.

## API

- `GET /clubs/{club_id}/hand-of-the-day/{game_id}?small_blind={small_blind}&big_blind={big_blind}` reads the state and remaining time for that blind pair. Active club members may read it.
- `PATCH /clubs/{club_id}/hand-of-the-day/{game_id}` enables or disables a blind-level promotion. Owners/managers send `{"enabled": true, "small_blind": 1, "big_blind": 5}`. The prize is calculated by the server.
- `POST /clubs/{club_id}/hand-of-the-day/{game_id}/fund` transfers treasury chips into the shared pool. Include `amount`, an idempotent `transaction_id`, `small_blind`, and `big_blind`.
- `POST /clubs/{club_id}/hand-of-the-day/{game_id}/claim` claims an expired tied-winner share. Include the `cycle_id` from the status response and the same blind pair.

See [API.md](API.md) for request/response examples and [database/README.md](database/README.md) for the pool and audit schema.
