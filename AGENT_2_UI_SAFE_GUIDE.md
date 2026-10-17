# Agent 2: Safe / Non-Authoritative Server Layer Guide

This document defines the second agent's boundary inside the game server, not just in the UI. The split applies to the backend itself: the authoritative engine and wallet logic remain the source of truth, while the safe layer exposes read-only or derived views and façades.

## Core rule

The boundary is based on behavior, not file location.

Agent 2 is responsible for the safe, non-authoritative layer.

It may build:
- read-only projections and summaries
- server-side lobby / table status views
- derived dashboard and ops reporting
- read models for club, union, and table lists
- API docs and client-facing contract descriptions
- safe façade routes that expose derived information without mutating authoritative state

It must not change:
- authoritative wallet balance truth
- chip movement or settlement logic
- table ownership / leasing
- table runtime state
- seat buy-ins or live game actions
- authoritative ledger creation or accounting writes
- checkpoint, failover, or persistence logic for real runtime state
- any write route that creates or changes real club, wallet, or table state

If a task changes state, money, ownership, or a live game outcome, it belongs to Agent 1.

> File placement alone is not a safe boundary. A router can contain both read-only GETs and authoritative writes; route semantics determine ownership.

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

Even inside [app/main.py](app/main.py), this rule still applies: read-only lobby or projection logic may be developed by Agent 2 only when it stays non-authoritative, but every route that mutates money, table ownership, club state, wallet state, or live runtime state remains Agent 1-owned.

### Agent 2 owns the safe server layer

Agent 2 is responsible for:
- lobby and table status read models
- member dashboards and read-only wallet summaries
- derived projections for UI and ops screens
- initial club-request or pre-approval flows only when they are explicitly non-authoritative
- server-side safe façade routes that read aggregated state without mutating it
- API docs and client-facing contract examples
- operational reporting, summaries, and monitoring data that are not authoritative

Agent 2 should not claim “safe” status for any route that can create real club state, treasury state, wallet state, or ownership state.

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

Agent 2 may support flows like a pre-creation proposal or staging form, but not a route that creates real club state.

The rule is strict:
- if the route creates a real club, owner membership, or funded treasury, it is Agent 1-owned
- if the route only stages intent or draft state, it may be treated as an Agent 2 workflow

Examples:
- `POST /platform/clubs` is not a safe Agent 2 route when it creates a real club and funded treasury
- a UI-only “proposal” or “draft club request” can be Agent 2-only if it is explicitly non-authoritative and never writes live funded state

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
- create or update authoritative accounting rows
- settle a hand
- move chips in/out of play
- assign or revoke table ownership
- claim or renew a table lease
- start or resolve a hand from the client layer
- bypass the authoritative backend with optimistic writes
- create accounting logs that represent real money movement
- expose a write endpoint as “safe” just because it is in a router with read-only GETs

If a task touches any of the above, it must be handed to Agent 1.

---

## The decision rule

Use this rule on every ticket:

- If it changes state, ownership, money, or gameplay -> Agent 1
- If it reads state, displays data, or prepares a safe user flow -> Agent 2
- If the route mutates real club, wallet, or table state, it is Agent 1 regardless of file location

If the task is ambiguous, default to Agent 1 until it is explicitly marked as non-authoritative and safe.

---

## Practical examples

### Allowed
- Show the current club member role
- Display table list with status and seat count
- Render a “request to create club” draft form
- Provide a “view-only ledger” with clearly marked informational use
- Return a read-only lobby projection built from a server GET

### Not allowed
- Creating a real club record or funded treasury
- Creating a reserve-backed grant or authoritative wallet write
- Claiming a stake in a player's balance
- Setting table lease ownership from a client request
- Updating chip balances on behalf of the server
- Writing derived state back into authoritative tables or ledgers
- Any endpoint that is presented as action-taking when the backend remains authoritative

---

## Merge and handoff rules

1. Agent 2 should not edit authoritative runtime or persistence files.
2. Agent 1 should own every route that mutates real club, wallet, or table state.
3. Shared contracts must be reviewed together before merging.
4. A route with write semantics is Agent 1-owned even if it sits in a router that also includes GET endpoints.
5. Documentation must match the actual route behavior, especially for reserve grants and club creation.

---

## Recommended boundary for this repo

Safe scope for Agent 2:
- read-only projection modules and summarization endpoints
- lobby and dashboard read models
- read-only wallet summary views
- documentation and contract reconciliation work
- client-facing examples that are explicitly marked as informational or non-authoritative

Within [app/main.py](app/main.py), Agent 2 may work only on read-only or derived route logic that does not write authoritative state. Any POST/PUT/PATCH/DELETE, lease claim, settlement, wallet debit/credit, or table lifecycle path stays under Agent 1 ownership and must be coordinated before merging.

Authoritative scope for Agent 1:
- [app/main.py](app/main.py)
- [logic](logic)
- [database](database)
- [game_engine](game_engine)
- wallet/accounting logic and settlement flows
- all mutation paths and persistence entry points
- club creation, reserve-backed grants, treasury changes, and ownership/lease writes

---

## Agent 2 backlog for this repo

A practical backlog for Agent 2 should be:

- reconcile API/UI docs and examples with actual route behavior, especially reserve grants and club creation
- confirm which POST routes are authoritative vs non-authoritative
- add focused contract tests for read-only routes, permissions, and lobby projection fields
- improve read-only dashboard or lobby projections while keeping them derived from Agent 1-owned state
- keep UI implementation in the client repo rather than this backend repo

---

## Short version

Agent 2 is for safe read-only projections, derived summaries, and documentation work.
Agent 1 is for money, ownership, gameplay truth, persistence, and every real mutation path.

The important correction is that file boundaries are not the real rule; route behavior and authoritative writes are the real rule.
