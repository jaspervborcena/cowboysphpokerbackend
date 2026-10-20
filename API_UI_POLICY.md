# UI API Authority and Exposure Policy

This document defines which backend routes are safe to call from the frontend UI, which ones are restricted to owner/admin bootstrap flows, and which ones must remain server-authoritative.

The rule is simple:

- The UI is a presentation and orchestration layer.
- The backend is the authority for identity, role checks, wallet state, and money movement.
- Any endpoint that can create or modify club state, treasury state, or member balances is authoritative and must not be treated as client-trusted logic.
- Initial grants are reserve-backed. The server debits the platform reserve wallet, credits the destination wallet, and writes an accounting log with `from_wallet_id`, `to_wallet_id`, `amount`, `currency_type`, and timestamp metadata.

---

## 1. Core policy

### 1.1 UI-safe routes

The frontend may only call routes that do the following:

- read public or authenticated state
- create a user session or subscription flow
- create a seat or table only when the backend validates the user and role
- read current wallet, role, table, and replay state

These routes are allowed in the browser when the authenticated Firebase user is valid and the request is otherwise authorized.

### 1.2 Server-authoritative routes

Routes that change club funds, create club-wide treasury state, or create canonical business records are server-authoritative. The browser must not be allowed to invent or calculate those mutations.

This includes:

- platform club creation
- club treasury transfers
- member send-out funding
- member claim-back funding
- any wallet mutation that must pass server validation, idempotency, and audit rules

### 1.3 Owner-only bootstrap exception

`POST /platform/clubs` is an exception only for bootstrap/admin creation of a club. It must not be treated as a normal self-service player-facing endpoint.

For this repository, the policy is:

- allowed only for trusted owner/admin bootstrap flows
- never exposed as a general public client route
- never open to arbitrary user registration or club creation by regular players
- still considered authoritative because it creates the club record, owner membership, and reserve-backed treasury grant
- must be implemented as an internal server operation even when a UI flow triggers it

### 1.4 Internal worker coordination

The game server's `/workers/{worker_id}/...` coordination routes are not UI-safe. They require the configured `WORKER_COORDINATION_TOKEN` bearer token, must reject mismatched path/payload worker IDs, and require an active worker session before claim/renew/release operations. Do not call them from a browser or expose them through public ingress; they are internal service routes and remain separate from player-facing API flows. When Firestore is enabled, the runtime can enforce the scoped lease operations through the durable repository, but cross-instance ownership and recovery remain the next hardening milestone.

---

## 2. Endpoint classification

### 2.1 Public or player-safe UI flows

These are UI-safe reads and flows when authenticated:

- `POST /me/wallets/global-playmoney/ensure`
- `GET /me/wallets/global-playmoney`
- `GET /clubs/{club_id}/members/me`
- `GET /clubs/{club_id}/members/me/wallet`
- `GET /clubs/{club_id}/tables`
- `GET /clubs/{club_id}/unions`
- `GET /clubs/{club_id}/unions/{union_id}/tables`
- `POST /clubs/{club_id}/tables/{table_id}` for authorized owner/manager table creation
- `POST /clubs/{club_id}/tables/{table_id}/seats` for a player buy-in
- `GET /clubs/{club_id}/hands/{hand_id}/replay`
- `GET /clubs/{club_id}/wallet` for owner/manager treasury read

These reads and actions are safe for the UI because the backend remains the authority and validates every request.

The following rake-report reads are restricted operator views, not player-safe routes: owners/managers can see club-wide player summaries; agents can see only their currently assigned active players; players are denied. Do not include rake-attribution data in player wallet or replay responses.

- `GET /clubs/{club_id}/rake/players`
- `GET /clubs/{club_id}/rake/players/{user_id}/hands`

### 2.2 Owner/admin bootstrap-only routes

These are not normal player routes and must be protected behind a strict bootstrap/admin path:

- `POST /platform/clubs`

Policy:

- owner-only or trusted admin-only access
- not exposed to arbitrary users
- not a frontend “create club” feature for normal members
- still authoritative because it creates the club, initial owner membership, and treasury grant

### 2.3 Authoritative wallet operations

These routes remain server-authoritative and must never be treated as client-trusted UI mutations:

- `POST /clubs/{club_id}/wallet/send-out`
- `POST /clubs/{club_id}/wallet/claim-back`
- `POST /clubs/{club_id}/wallet/transfers`

Why these remain authoritative:

- they move chips between balances
- they enforce role and downline rules
- they validate available balance; claim-back only debits `chip_balance` and never changes `in_play_balance`
- they use transaction boundaries and idempotency rules
- they create audit history and ledger records

The frontend may initiate or display these actions, but the backend must perform the actual balance mutation.

---

## 3. Rules for implementation

### 3.1 Frontend behavior

The frontend must:

- read balances from the backend instead of calculating them locally
- show current role from `GET /clubs/{club_id}/members/me`
- show wallet state from `GET /clubs/{club_id}/members/me/wallet`
- use server results as the source of truth
- never trust a client-supplied amount as authorized money movement

### 3.2 Backend behavior

The backend must:

- verify Firebase authentication on all protected requests
- verify active membership and role before allowing club actions
- validate amount, member existence, role, and in-play constraints
- enforce idempotency with transaction IDs where required
- write audit/ledger records atomically
- fail closed when Firestore or auth is unavailable

### 3.3 Money-changing endpoints must remain locked down

The following operations are not generic UI features:

- send-out
- claim-back
- inter-club transfer
- platform club creation

These must remain under internal administrative or owner-controlled flows.

---

## 4. Decision matrix

| Route | UI exposure | Authority level | Allowed caller |
|---|---|---|---|
| `POST /me/wallets/global-playmoney/ensure` | Yes | Server-authoritative for provisioning, but user-scoped | Authenticated user |
| `GET /me/wallets/global-playmoney` | Yes | Read-only | Authenticated user |
| `GET /clubs/{club_id}/members/me` | Yes | Read-only | Authenticated member |
| `GET /clubs/{club_id}/members/me/wallet` | Yes | Read-only | Authenticated member |
| `POST /clubs/{club_id}/tables/{table_id}` | Yes | Server-authoritative table creation | Owner/manager |
| `POST /clubs/{club_id}/tables/{table_id}/seats` | Yes | Server-authoritative seat funding | Player |
| `GET /clubs/{club_id}/wallet` | Yes | Read-only treasury view | Owner/manager |
| `POST /platform/clubs` | Only bootstrap/admin | Authoritative club creation | Owner/admin bootstrap path only |
| `POST /clubs/{club_id}/wallet/send-out` | UI may trigger, but backend must enforce | Authoritative money move | Owner/manager/assigned agent |
| `POST /clubs/{club_id}/wallet/claim-back` | UI may trigger, but backend must enforce | Authoritative money move | Owner/manager/assigned agent |
| `POST /clubs/{club_id}/wallet/transfers` | No general UI access | Authoritative treasury move | Owner/manager |
| `GET /clubs/{club_id}/rake/players` | Restricted operator report; never expose to players | Read-only private club accounting | Owner/manager; agents see assigned players only |
| `GET /clubs/{club_id}/rake/players/{user_id}/hands` | Restricted operator report; never expose to players | Read-only private club accounting | Owner/manager/assigned agent |

---

## 5. Required working policy for this project

For this project, the approved rule is:

- The UI may read wallet and role state.
- The UI may create a seat or table when the backend validates authorization.
- The UI may show send-out and claim-back actions as flows, but the server must perform the actual ledger mutation.
- `POST /platform/clubs` is not public UI functionality. It is an owner/admin bootstrap operation only.
- No general player or public client can create clubs or modify treasury state.

---

## 6. Implementation checklist

Use this as the implementation checklist for UI and backend work:

### UI checklist

- [ ] Only call backend routes that are listed as UI-safe
- [ ] Read member role from the server
- [ ] Read wallet balance from the server
- [ ] Never compute authoritative balances in the browser
- [ ] Treat send-out and claim-back as server-authoritative actions
- [ ] Keep club creation and treasury creation off the public client path

### Backend checklist

- [ ] Verify Firebase token on protected routes
- [ ] Verify active membership
- [ ] Verify correct role
- [ ] Validate request amount and amount constraints
- [ ] Enforce idempotency and audit writes
- [ ] Reject unauthorized or out-of-balance operations
- [ ] Keep `POST /platform/clubs` restricted to owner/admin bootstrap only

---

## 7. Final rule to follow

The browser can orchestrate the flow, but it cannot be the authority for money movement or club creation.

That means:

- read-only wallet and role endpoints are okay in the UI
- table creation and seat purchase are okay when validated by the backend
- club creation and treasury movement remain restricted, authoritative, and owner/admin-controlled
- no general public UI path for `POST /platform/clubs`

This is the rule to implement and enforce across frontend and backend work.
