# Agent 2: Safe / Non-Authoritative Server Layer Guide

This document defines the second agent's boundary inside the game server, not just in the UI. The split applies to the backend itself: the authoritative engine and wallet logic remain the source of truth, while the safe layer exposes read-only or non-authoritative views and façades.

## Core rule

Agent 2 is responsible for the safe, non-authoritative layer.

It may build:
- read-only projections and summaries
- server-side lobby / table status views
- non-authoritative club setup or pre-approval flows
- read models, dashboards, and operational reporting
- API docs and client-facing contract descriptions
- safe façade routes that expose derived information without mutating the authoritative state

It must not change:
- authoritative wallet balance truth
- chip movement or settlement logic
- table ownership / leasing
- table runtime state
- seat buy-ins or live game actions
- any server-side accounting that changes authoritative balances
- checkpoint, failover, or persistence logic for real runtime state

If a task changes state, money, ownership, or a live game outcome, it belongs to Agent 1.

---

## Ownership boundary

### Agent 1 owns the authoritative system

Agent 1 is responsible for:
- table creation and lifecycle
- table ownership / lease claims
- game actions and table state transitions
- wallet updates and settlement
- ledger / accounting writes
- server-side validation and persistence
- failover, checkpoints, and runtime safety

### Agent 2 owns the safe server layer

Agent 2 is responsible for:
- lobby and table status read models
- member dashboards and read-only wallet summaries
- derived projections for UI and ops screens
- forms that request initial club creation only when explicitly non-authoritative
- server-side safe façade routes that read aggregated state without mutating it
- API docs and client-facing contract examples
- operational reporting, summaries, and monitoring data that are not authoritative

---

## Allowed work for Agent 2

### 1. Read-only queries

These are safe because they do not change state:
- `GET /health`
- `GET /games`
- `GET /clubs/{club_id}/tables`
- `GET /clubs/{club_id}/tables/{table_id}`
- `GET /clubs/{club_id}/unions`
- `GET /clubs/{club_id}/unions/{union_id}/tables`
- `GET /unions/{union_id}/tables/{table_id}`
- `GET /clubs/{club_id}/members/me`
- `GET /clubs/{club_id}/members/me/wallet`
- `GET /me/wallets/global-playmoney`

These can be used to populate dashboard screens and UI state, provided they are not treated as the source of truth for money movement.

### 2. Initial non-authoritative club creation

Agent 2 may support flows like:
- `POST /platform/clubs`

only when the task is explicitly limited to:
- initial club setup
- pre-approval or proposal creation
- user-intent capture before authoritative server processing
- UI-only staging or draft state

This is allowed only if the route is documented as non-authoritative and does not trigger a real balance or treasury mutation.

### 3. Safe façade and projection work

Agent 2 may build:
- dashboard navigation and lobby projections
- club and table cards backed by read-only data
- role display and permissions messaging based on authoritative lookup only
- safe client/server validation for non-authoritative requests
- form completion state and draft flows
- loading, empty, error, and retry UX
- operational summaries and derived reads for monitoring or UI consumption

### 4. Documentation and API contract work

Agent 2 may update:
- API docs
- client request examples
- UI usage guides
- rules for safe client behavior
- explanation of authoritative vs non-authoritative routes

---

## Forbidden work for Agent 2

Agent 2 must not do any of the following:

- send chips or fund member balances
- claim chips back from a player
- debit or credit club treasury
- mutate a wallet balance
- settle a hand
- move chips in/out of play
- assign or revoke table ownership
- claim or renew a table lease
- start or resolve a hand from the client layer
- bypass the authoritative backend with optimistic writes
- create accounting logs that represent real money movement

If a task touches any of the above, it must be handed to Agent 1.

---

## The decision rule

Use this rule on every ticket:

- If it changes state, ownership, money, or gameplay -> Agent 1
- If it reads state, displays data, or prepares a safe user flow -> Agent 2

If the task is ambiguous, default to Agent 1 until it is explicitly marked as UI-safe and non-authoritative.

---

## Practical examples

### Allowed
- Show the current club member role
- Display table list with status and seat count
- Render a “request to create club” form
- Provide a “view-only ledger” with clearly marked informational use

### Not allowed
- Creating a real treasury transfer
- Claiming a stake in a player's balance
- Setting table lease ownership from a client request
- Updating chip balances on behalf of the server
- Writing derived state back into authoritative tables or ledgers
- Any endpoint that is presented as action-taking when the backend remains authoritative

---

## Merge and handoff rules

1. Agent 2 should not edit authoritative runtime or persistence files.
2. Agent 1 should not edit UI-only flows unless the purpose is to expose a missing authoritative route.
3. Shared contracts must be reviewed together before merging.
4. Any route that has write semantics must be assigned to Agent 1 and documented as authoritative.

---

## Recommended boundary for this repo

Safe scope for Agent 2:
- [app/api](app/api)
- [API.md](API.md)
- [UI_GUIDE.md](UI_GUIDE.md)
- [README.md](README.md)
- read-only projection or façade modules used by the app and dashboard layer
- client-side mock or UI implementation files

Authoritative scope for Agent 1:
- [app/main.py](app/main.py)
- [logic](logic)
- [database](database)
- [game_engine](game_engine)
- wallet/accounting logic and settlement flows
- all mutation paths and persistence entry points

---

## Short version

Agent 2 is for safe read-only projections, façade routes, and non-authoritative flows.
Agent 1 is for money, ownership, gameplay truth, persistence, and runtime mutation.

That separation keeps both agents from interfering with each other while preserving a clean authoritative boundary inside the game server itself.
