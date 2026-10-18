# Firestore UI Schema and Sample Data

This document is the UI handoff for current API/storage behavior and planned contract additions. Its current-storage claims are checked against this backend repository's source, docs, and demo dataset in [database/fixtures/firestore_demo_seed.json](database/fixtures/firestore_demo_seed.json); that does not independently certify a deployed Firebase project's live documents. In a frontend-only checkout, use the checked-in API contract/OpenAPI for client behavior and treat Firestore implementation details here as supplied backend documentation. Treat every section marked planned/not implemented as a proposal, not an available API or Firestore field. UI clients should use documented APIs/WebSockets for protected state and must not write authoritative wallet, membership, or game data directly to Firestore.

This is also the UI contract update plan: it tells the frontend team which names are canonical, which names are legacy aliases, and which collections/fields should be added next without changing the actual Firestore documents yet.

## Important notes

- Firestore has a fixed maximum document size of 1 MiB; plans cannot raise it. Keep business documents small and flat: one document per record, scalar fields and IDs that reference related records, not embedded child-document objects, member lists, ledger arrays, or inventory arrays.
- For new schema work, prefer top-level collections with explicit scope IDs (for example, `club_uuid`, `club_id`, `player_id`, `wallet_id`, and `table_id`) over deeply nested collection paths. Existing nested collection paths below describe the current backend and are not being silently changed by this plan.
- Flat child records must still carry the parent document ID as a required reference field. Moving a child to a top-level collection removes the parent ID from its path, so copy it into the child document (for example, a club-scoped record includes both `club_uuid` and `club_id`; `wallet_ledger/{entry_id}` includes `wallet_id`; a member transaction includes `player_id`). Keep these IDs even when the child also has its own document ID.
- Firestore subcollections are separate documents, not documents physically embedded in their parent. The current backend uses some subcollections; future flat-path changes require a deliberate backend migration.
- `CLUB_CHIPS` are the only currency for club-table gameplay, including union tables connected to clubs. A union table does not introduce a separate playable currency; buy-ins debit the player's selected home-club balance.
- `DIAMONDS` and `GLOBAL_PLAYMONEY` are separate from `CLUB_CHIPS` and cannot be used directly as a table stack or buy-in. Diamonds may only fund gameplay after an explicit, backend-authorized conversion to club chips.
- Current API `user_id` is the Firebase Auth UID derived from the verified token. `player_id` is the planned 8-digit business identity for invitations, memberships, and player-facing records; the current API does not yet return or accept it for these flows. Do not fabricate `player_id` by copying `user_id`.
- The current member API returns `username`, not `nickname`; `nickname` is planned. Use the returned username until nickname support is added, then display `nickname`, `display_name`, `username`, and generated fallback in that order.
- Use one canonical spelling: `user_id`, `created_at`, `updated_at`, `created_by`, and `updated_by`. Do not create parallel camelCase variants such as `userId`, `createdAt`, `createAt`, `updatedAt`, `createdBy`, or `updatedBy` in new documents.
- `nickname` is the display label used at the table; if no nickname exists, the UI should fall back to `display_name`, `username`, or a default generated name.
- `transaction_id` is the canonical ledger/transfer record ID. A separate `trade` collection is optional only if the backend has a dedicated trade workflow; otherwise, `transaction_id` is the real authoritative ID.
- In current member documents, `chip_balance` is the member's available club-chip balance; the wallet API exposes it as `available_balance`. `in_play_balance` is the member's total club chips reserved into table seats. Each table seat's `stack` is that player's balance at that specific table. Do not map `chip_balance` to a table stack.
- Current membership authorization reads `status == "active"` and `role`; `member_status` is a proposed future field, not currently returned by the API.
- The target model described in [database/README.md](database/README.md) separates `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, `CLUB_AGENT`, `CLUB_PLAYER`, and `TABLE_ESCROW`, but the live repo is not there yet.
- Raw hand audit documents are backend-only and must not be exposed to the UI.
- Runtime ownership and lease state (`.../runtime/lease`, `.../runtime/state`) are internal backend tracking and not UI business data.
- This document is a contract and migration plan; it does not require a schema rewrite in the live Firestore database today.

## Identity and business model

The most important rule is that the app uses two different identity types:

- `user_id`: Firebase Auth UID, used for authentication and current API membership/member document keys
- `player_id`: planned business identity for invitations, table seats, ownership, and player-facing business records; not currently supported by the API

Keep these identities separate when backend support for `player_id` is added. Until then, send the signed-in Firebase ID token and let the API derive `user_id`; do not send either ID as proof of identity.

### User profile and ID mapping (planned)

Use one flat top-level profile document at `users/{user_id}`. The document ID and its `user_id` field are the Firebase Auth UID; `player_id` is a separate, server-assigned, unique 8-digit business ID. Keep the profile keyed by `user_id` so Firestore Rules can bind it to `request.auth.uid`; do not rekey the profile to `player_id`.

```json
{
  "user_id": "firebase-auth-uid",
  "player_id": "12345678",
  "username": "player-name",
  "nickname": "table-name",
  "created_at": "Firestore server timestamp",
  "updated_at": "Firestore server timestamp",
  "created_by": "firebase-auth-uid",
  "updated_by": "firebase-auth-uid"
}
```

Generate `player_id` on the trusted backend and enforce uniqueness there; never let the client choose it or derive it from the UID. Invitations and public member search may identify a person by their 8-digit `player_id`; the backend resolves that to `user_id`. When an authenticated user joins a club, derive their `user_id` from the Firebase token and load their assigned `player_id` from the profile; do not trust either value from the request body.

An optional referral/agent reference uses one canonical field, `agent_player_id`: the referring agent's 8-digit `player_id`. It is optional during join/invitation acceptance. The backend must verify the referenced player exists, has the AGENT role, and may sponsor members in that club, then resolve it to the internal UID needed by current legacy logic. Do not send both `agent_id` and `referral_id` as duplicate fields. The current Firestore `agent_id` stores a Firebase UID; `agent_player_id` is the planned API/business field and requires backend migration.

Do not embed club memberships in the user document; keep them in separate membership records with explicit `user_id`, `player_id`, `club_uuid`, `club_id`, and optional `agent_player_id` references.

The Firebase console screenshot shows a top-level `users` collection, but this backend repo's fixture/API does not define its document fields or return this ID mapping. Treat the shape above as the target contract until the deployed collection and backend behavior are verified and migrated.

### Current API payloads

`GET /clubs/{club_id}/members/me` currently returns only `club_id`, Firebase `user_id`, `username`, and `role`. It does not return `player_id`, `nickname`, or `member_status`.

`GET /clubs/{club_id}/members/me/wallet` returns `club_id`, Firebase `user_id`, `role`, `currency_type: "CLUB_CHIPS"`, `available_balance`, and `in_play_balance`. The API maps the stored member `chip_balance` to `available_balance`.

**UI contract:** use the API's `available_balance` as the player's spendable club-chip balance. Do not read Firestore `chip_balance` directly or remap it in the client. Render `in_play_balance` separately; use the seat/table snapshot's `stack` for chips at a specific table.

Table and union seat snapshots use each seat's `stack` for the balance at that table. Club-chip buy-in moves value from member `chip_balance` to `in_play_balance` and initializes the seat `stack`; the wallet balance and table stack are distinct values.

### Firestore ownership and audit fields

Use these common fields on new business documents:

| Field | Meaning | Rule |
| --- | --- | --- |
| `user_id` | Firebase Auth UID of the user who owns or is the subject of a user-scoped document | Include when the document has one user owner/subject. It may also be the document ID, but retain the field for simple rules and queries. Do not substitute `player_id`. |
| `created_at` | Creation time | Proposed canonical field for new/migrated records; set by the trusted backend/server. Current legacy transaction writers may use `timestamp`. |
| `updated_at` | Last modification time | Proposed canonical field, refreshed by the trusted backend/server on every permitted mutable-document update. |
| `created_by` | Firebase Auth UID of the creator | Proposed canonical field for new/migrated records; set from verified auth on the server, with an explicit system actor for system-created documents. Current legacy transaction writers may use `performed_by`. Immutable after creation. |
| `updated_by` | Firebase Auth UID of the last actor to modify the document | Set from verified auth on the server; use an explicit system actor for system updates. |

Include all four audit fields on mutable business records where applicable. For new/migrated immutable ledger and payment records, use `created_at` and `created_by` but no `updated_at` or `updated_by`; do not mutate an immutable record just to populate update fields. Preserve current legacy fields such as `timestamp` and `performed_by` until a backend migration is approved. For shared records without a single user owner, omit `user_id` rather than inventing an owner; use the actor/audit fields plus the relevant `club_id`, `table_id`, or `wallet_id` scope IDs.

Firestore Security Rules must compare `user_id`/`created_by` to `request.auth.uid` for client writes, prevent clients changing ownership or `created_by`, and validate `updated_by` and timestamps on updates. These fields are not security by themselves: rules must also check the operation and the user's membership/role. Server Admin SDK writes bypass Firestore Rules, so API handlers must derive actor UIDs from verified Firebase tokens and assign server timestamps; never trust client-supplied actor fields.

This is the target contract for new/migrated documents. Current collections are inconsistent and are not retroactively given these fields by this documentation change; existing documents need a deliberate migration before rules can require them.

### Generic tracing and audit event schema

Do not write generic application, security, or tracing logs to Firestore. Emit one structured JSON event to Google Cloud Logging; use a Cloud Logging sink to export selected events asynchronously to BigQuery. This keeps observability writes out of Firestore transactions and gameplay request paths. BigQuery is for querying/reporting, not synchronous authorization or balance decisions.

Use one stable, flat event envelope across REST, WebSocket, Pub/Sub, and worker logs. The suggested BigQuery table is `application_events`; use a top-level schema with scalar fields:

| Field | Meaning |
| --- | --- |
| `event_id` | Unique event ID (UUID). |
| `schema_version` | Integer version of this event shape. |
| `event_type` | Stable dotted name, such as `wallet.send_out`, `membership.role_changed`, `club.suspended`, `table.buy_in`, or `auth.access_denied`. |
| `event_timestamp` | Server timestamp when the event occurred. |
| `actor_type` | `user`, `service`, or `system`. |
| `user_id` | Actor's Firebase UID when `actor_type` is `user`; omit for service/system actors. |
| `player_id` | Actor's business ID when applicable; never use it in place of `user_id`. |
| `actor_role` | Server-resolved role at event time, if applicable. |
| `target_user_id` / `target_player_id` | Affected user/business identity, when applicable. |
| `club_id`, `union_id`, `table_id`, `wallet_id` | Applicable scope/parent document IDs for filtering and authorization audits. |
| `resource_type`, `resource_id` | Primary affected resource type and document ID. |
| `trace_id` | Correlation ID shared with the API response and structured application logs. |
| `request_id`, `action_id`, `transaction_id` | Existing request/action/idempotency IDs, when applicable. |
| `source_service` | Service/component that emitted the event. |
| `outcome` | Stable result such as `success`, `denied`, `failed`, or `idempotent_replay`. |
| `error_code` | Stable numeric application code when the event failed; otherwise omit. |
| `duration_ms` | Server-measured operation duration when applicable. |
| `reason_code` | Safe, bounded reason identifier for moderation/security decisions; avoid raw user text by default. |

Example:

```json
{
  "event_id": "4d3e80a6-48bc-4e29-9c42-1b7590a0a6b1",
  "schema_version": 1,
  "event_type": "wallet.send_out",
  "event_timestamp": "2026-10-18T12:00:00Z",
  "actor_type": "user",
  "user_id": "firebase-uid-agent",
  "player_id": "12345678",
  "actor_role": "AGENT",
  "target_user_id": "firebase-uid-player",
  "target_player_id": "22345678",
  "club_id": "club-1",
  "resource_type": "club_transaction",
  "resource_id": "transaction-uuid",
  "trace_id": "req_4d3e80a648bc4e299c421b7590a0a6b1",
  "request_id": "request-uuid",
  "action_id": "action-uuid",
  "transaction_id": "transaction-uuid",
  "source_service": "wallet-api",
  "outcome": "success",
  "duration_ms": 42
}
```

Export meaningful business/security events to BigQuery: wallet/diamond movement (reference the authoritative financial record using `transaction_id`; do not duplicate balances), buy-ins/cash-outs, role and membership changes, kicks/suspensions, club/union lifecycle, payment/refund decisions, and authorization denials. Routine successful reads, worker heartbeats, and high-volume gameplay diagnostics may remain in Cloud Logging and only be exported if needed. Configure BigQuery partitioning by `DATE(event_timestamp)` and clustering on frequently filtered fields such as `event_type`, `club_id`, and `trace_id`; define retention, access controls, and cost limits before production rollout.

Firestore financial ledger documents remain authoritative business records written atomically with balance changes; they are not generic logs and must not be replaced by delayed BigQuery exports. Do not log Firebase tokens, passwords, payment-card/bank details, full request bodies, raw GPS coordinates, private hole cards, deck state, or unbounded exception/user text. This schema is proposed, not implemented; current error logs already include a `trace_id`, but successful requests and domain events are not uniformly correlated.

### Display naming rule

Use this order when a table row or member card needs a label:

1. `nickname`
2. `display_name`
3. `username`
4. fallback generated name like `Player 12345678`

This keeps the table display friendly while preserving a reliable UI fallback.

### Current balance semantics

- Member `chip_balance` = available club chips in the current Firestore storage model; API name is `available_balance`.
- `in_play_balance` = club chips reserved across table seats, not the stack at a single table.
- Seat `stack` = chips belonging to this player at this specific table.

Use the wallet API for available/in-play summary and the table snapshot for a seat's current stack. Never add or substitute these fields for one another.

### Status rule

`status` is context-dependent by collection. Today, active club membership authorization reads the member document's `status` value (`active`). A dedicated `member_status` is planned, but not currently returned by the API. Do not assume `status` has the same meaning on a club, table, ledger, and membership record.

### Ownership rule

Interpret owner fields according to the endpoint/collection contract. Current `POST /platform/clubs` uses `owner_user_id` as a Firebase Auth UID. A future business-owner reference should use `owner_player_id` only after the backend explicitly supports it; do not relabel an existing UID.

### Trade vs transaction rule

There is no need to force a dedicated `trade` collection unless the backend implements a true trade workflow. In the present architecture, `transaction_id` is the canonical ledger transfer ID. `trade_id` may exist as a legacy alias or UI-only label, but it is not a separate source of truth unless the backend explicitly adds a trade entity.

## Canonical UI names to standardize on

Normalize names in frontend types, but only populate fields the source API actually returns. Optional planned fields must remain absent until backend support is deployed.

### Canonical identity fields
- `user_id` = Firebase Auth UID; current API identity
- `player_id` = planned 8-digit business identity; backend support pending
- `club_uuid` = immutable internal UUID, Firestore document ID, and operational API path parameter
- `club_id` = planned 8-digit public/business ID for search, invitations, and display; current API support is pending
- `table_id` = table ID
- `union_id` = union ID
- `wallet_id` = wallet record ID
- `nickname` = planned table display name; current API returns `username`

### Club ID migration mapping (planned; backend migration required)

Use `club_uuid` as the Firestore document key and as the path parameter for authenticated club operations. Keep existing UUID-keyed documents and subcollections; do not rekey them. For each club, the backend should:

1. Keep or assign the UUID as `club_uuid`, matching the `clubs/{club_uuid}` document ID.
2. Allocate a unique 8-digit business `club_id` and store it separately on the club document.
3. Create a flat `club_id_index/{club_id}` lookup document containing `club_id` and `club_uuid` for search and invitation resolution.
4. Return both IDs from APIs. Use `club_uuid` in routes for authenticated operational requests, so the backend can address the club document directly without looking up the business ID.

Before the new contract ships, frontend code must treat the current UUID-valued `club_id` as opaque. Do not parse it, relabel it as either target ID, or derive the 8-digit business ID from it. After migration, use `club_uuid` for club API/WebSocket paths and `club_id` only for search, invitation, and display flows. Neither ID grants access by itself; backend authorization must still verify membership/role.

#### Route contract (planned)

- Use `/clubs/{club_uuid}/...` for operational REST routes and `/ws/table/{club_uuid}/{table_id}` for local gameplay sockets; these values directly identify `clubs/{club_uuid}`.
- For union sockets, authenticate with the player's 8-digit home `club_id`; resolve it through `club_id_index` to `club_uuid` before membership checks. Keep `union_id` as its existing identifier.
- `POST /platform/clubs` generates a UUID `club_uuid` and a collision-safe 8-digit `club_id`, then returns both. Club-scoped responses include both fields.
- Public club search and invitations accept `club_id`; the backend resolves it through the index. UUIDs and 8-digit IDs are never guessed or silently interchanged.

This is a proposed backend/API change, not current behavior. Current endpoints still accept UUID-valued `club_id` parameters.

### Canonical business fields
- `username` / `display_name`
- `nickname` (planned; current membership endpoint returns `username`)
- `role`
- `status` (collection-specific; not included in current `GET /members/me` response)
- `member_status` = planned explicit member lifecycle field; current member document uses `status`
- `membership_status` when the UI needs a broader membership lifecycle state outside the member record
- `wallet_type`
- `currency_type`
- `club_uuid` (planned stable internal club reference)
- `agent_player_id` (optional planned 8-digit agent/referral `player_id`; not the current stored `agent_id` UID)
- `chip_balance` (stored member available balance; API maps to `available_balance`)
- `in_play_balance`
- `available_balance`
- `transaction_id`
- `trade_id` as an optional UI alias, not the canonical persisted field
- `amount`
- `type`
- `note`
- `created_at`
- `updated_at`

### Canonical naming rule

Use this naming hierarchy:
1. Keep the raw Firestore field as-is in storage.
2. Map it to a canonical UI field in the frontend type layer.
3. Do not force a raw storage rename unless the backend intentionally changes the schema.

This prevents duplicate records or duplicate fields from being created in the same flow while the app matures.

## Legacy-to-canonical alias map

This is the minimum alias map the UI team should implement before they rename any screen fields.

| Legacy storage field | Canonical UI field | Notes |
| --- | --- | --- |
| `userId` / `firebase_uid` / `uuid` | `user_id` | Map only when the value is verified to be the Firebase Auth UID. Keep `player_id` separate; never infer it from a UUID. |
| `createdAt` / `createAt` | `created_at` | Canonical server timestamp. |
| `updatedAt` | `updated_at` | Canonical server timestamp. |
| `createdBy` / `createBy` | `created_by` | Canonical creator Firebase UID or explicit system actor. |
| `updatedBy` | `updated_by` | Canonical last-updater Firebase UID or explicit system actor. |
| `timestamp` | Context-dependent | Current transfer writer field; normalize to `created_at` only when the source confirms it is the document creation time. |
| `performed_by` | Context-dependent | Current transfer actor field; do not assume it is a general `created_by` field in every collection. |
| `user_id` | `user_id` | Current API value is the Firebase Auth UID. Never reinterpret or copy it into `player_id`. |
| `player_id` | `player_id` | Canonical business identity for membership, invites, seats, and ownership. |
| `agent_id` (current Firestore member field) | `agent_player_id` | Current stored `agent_id` is a Firebase UID. Backend must resolve it to the agent's 8-digit `player_id`; frontend must not copy or relabel the UID. |
| `club_id` (current UUID-valued API/path field) | Opaque legacy ID until backend migration | Frontend must not infer either target ID from it. |
| Existing club document/path UUID | `club_uuid` | Preserve as the Firestore document ID and operational API path parameter. |
| Backend-issued 8-digit business club ID | `club_id` | Read only from the migrated API/document field; never derive it client-side. |
| `owner_user_id` | Context-dependent | Preserve the current collection/API meaning. For `POST /platform/clubs`, it is a Firebase UID; only map to `owner_player_id` when a future contract confirms it is the business player ID. |
| `chip_balance` on a member document | `available_balance` | Current wallet API performs this mapping. It is not the table seat stack. |
| `stack` on a seat/snapshot | `stack` | Current per-table player stack. |
| `available_balance` | `available_balance` | Canonical wallet balance value. |
| `in_play_balance` | `in_play_balance` | Keep as-is; it is already semantically clear. |
| `transaction_id` | `transaction_id` | Keep canonical ledger field. |
| `trade_id` | `transaction_id` | Optional legacy alias only. Do not create a separate source of truth unless a dedicated trade collection exists. |
| `status` | `status` | Preserve the collection-specific meaning; current membership authorization checks `status == "active"`, but the current membership API does not return this field. |
| `member_status` | `member_status` | Planned member lifecycle field; not currently supported by the API. |
| `membership_status` | `membership_status` | Use only where the broader membership lifecycle is needed; it should not replace `status` everywhere. |
| `wallet_id` | `wallet_id` | Keep canonical. |
| `wallet_type` | `wallet_type` | Keep canonical. |
| `currency_type` | `currency_type` | Keep canonical. |
| `from_user_id` / `to_user_id` | Preserve as user IDs | Current transfer ledger fields contain Firebase UIDs. Do not relabel them as player IDs until the backend provides business IDs. |
| `nickname` | `nickname` | Canonical display label for tables and member chips. |
| `club_id` | Context-dependent | Current API/path value is UUID-valued and opaque; after migration, `club_id` is the 8-digit public/business ID, while operational routes use `club_uuid`. |
| `table_id` | `table_id` | Stable canonical field. |
| `union_id` | `union_id` | Stable canonical field. |

## Recommended UI update workflow

Do not rename live Firestore documents in one pass. Instead:

1. Add frontend canonical types.
2. Map legacy Firestore records to those canonical types.
3. Update screen bindings to the canonical names.
4. Keep legacy aliases only in the mapping layer until the backend contract is explicitly changed.
5. Add new collections/fields only when the backend contract is ready.

This keeps the UI from generating duplicate naming patterns that can drift from the storage model.

## Collections and fields to add next

These are the next additions to make the UI contract match the newer business model, without forcing immediate server-schema rewrite.

### Planned wallet/account additions
- `wallet_accounts/{wallet_id}`
- `wallet_ledger/{entry_id}`

Purpose:
- separate wallet purpose from raw member balance
- unify account-level balance state for `DIAMONDS`, `GLOBAL_PLAYMONEY`, `CLUB_TREASURY`, `CLUB_AGENT`, `CLUB_PLAYER`, and `TABLE_ESCROW`
- keep each ledger entry as a small, flat document that references its wallet/account IDs

Every `wallet_ledger/{entry_id}` document must include `wallet_id` (the parent wallet document ID) and, for scoped operations, the relevant `player_id`, `club_id`, or `table_id`. The record stays top-level but retains its parent references.

### Planned Diamonds currency and premium catalog

Diamonds are a separate premium currency with player- and club-scoped wallet accounts. Proposed account/ledger fields include `wallet_type: "DIAMONDS"`, `owner_scope`, `owner_id`, `diamond_balance`, `transaction_id`, `type`, `amount`, `created_at`, and provider/payment references where applicable. Store account records at `wallet_accounts/{wallet_id}` and each ledger entry separately at `wallet_ledger/{entry_id}`; use ID references rather than embedding ledger entries in the account document or storing a second authoritative balance on member/club documents. Diamonds are never a playable table balance: club chips are the only buy-in and table currency for both club and union-connected games. Any Diamond-funded club chips require an explicit, server-authorized conversion before the buy-in.

Planned flows:
- payment-provider-confirmed Diamond purchase (cards, GCash, and other supported methods)
- Diamond transfers between explicitly permitted player and club accounts
- purchases of VIP/Black VIP entitlements, avatars, card-protection effects, emojis, and table themes
- club creation and plan/star upgrades
- conversion to club chips (example only: 200 Diamonds for 100,000 chips)
- purchase of additional global play-money chips

Proposed supporting data includes flat, top-level `diamond_payments/{payment_id}`, `diamond_purchases/{purchase_id}`, and `player_entitlements/{entitlement_id}` records. Each child record must carry its parent document ID as a field: for example, a payment or purchase carries `player_id` and `wallet_id`, while an entitlement carries `player_id`; club-scoped purchases also carry `club_id`. Store product/payment IDs, status, and timestamps as fields; do not embed purchases or entitlements as arrays in wallet documents. The backend must be the only authority that credits Diamonds, applies conversions, or grants items, and every balance change must be auditable and idempotent. The 200-to-100,000 example is not a fixed rate; product prices and allowed conversion directions must come from server configuration. Never merge Diamond, club-chip, and global-play-money balances.

These currency, payment, transfer, catalog, inventory, and conversion flows are not implemented. Define transferability, account scopes, provider verification, refund/chargeback handling, item duration/renewal, and plan purchase semantics before the UI depends on them.

### Planned membership lifecycle additions
- `club_invitations/{invitation_id}`
- `club_membership_requests/{request_id}`
- member lifecycle and moderation fields on member records (proposed: `member_status`, `suspension_reason`, `suspended_at`, `suspended_by`, `removal_requested_at`)
- club lifecycle and moderation fields on club records (proposed: `club_status`, `suspension_reason`, `suspended_at`, `suspended_by`, `close_after`)
- an audit trail for role changes, kicks, suspensions, union removals, and club closures

Purpose:
- formalize invite/accept flow
- differentiate auth identity from club membership status
- support manager/owner role controls and union-leader sanctions without erasing financial history

These fields are proposals only; they are not currently returned by the API or used by backend authorization. The backend must define the final lifecycle enum values and transition rules before the UI writes or depends on them.

### Planned club plan and capacity fields

Proposed fields on `clubs/{club_id}`:

- `plan_id`
- `club_star_level` (integer from 1 to 10)
- `member_limit` (plan-defined maximum; represent unlimited capacity explicitly in the final API contract)

The plan determines member capacity. A 10-star club may have a 5,000-member limit or unlimited capacity depending on its plan; never infer capacity from stars alone. Clubs need at least 3 stars to be eligible for union membership. The backend must validate capacity during member admission and validate the 3-star minimum when a club is linked to a union. These fields and rules are not implemented yet; exact plans, capacity mapping, and behavior when a plan is downgraded below the current membership count remain open decisions.

### Planned agent assignment and rakeback ownership

Use a flat top-level `agent_groups/{agent_group_id}` document scoped to one club. It should include `agent_group_id`, `club_uuid`, `club_id`, the original `agent_player_id`, and the current `acting_agent_player_id`. Keep player assignments in separate flat `agent_group_memberships/{membership_id}` documents with `agent_group_id`, `club_uuid`, `club_id`, and `member_player_id`; do not embed a growing `members`/`player_ids` array in the group document.

`agent_balance` is the group's operational club-chip balance used for send-outs and claim-backs; it is not the agent's personal player balance. Keep it separate from the group's `rakeback_balance`, which tracks accrued but undistributed rakeback. Both balances belong to the club-scoped group and must be updated through the server-side ledger transaction, not independently edited by the UI. On temporary manager takeover, change only `acting_agent_player_id`; keep the original `agent_player_id`, group membership assignments, both balances, and accrued rakeback attached to the same `agent_group_id`. If the original agent returns, restore the acting assignment to that same player ID/group. Rakeback is not paid to players automatically and remains available for explicit manual chip transfers.

This group, membership, wallet-link, and assignment-history model is not implemented in the current `agent_id` field, which currently points to a Firebase UID. It requires backend APIs and transaction rules before the UI should depend on it.

### Current club member transfers, including agents

Agent transfers are not a separate API or Firestore collection. The existing endpoints are shared by `OWNER`, `MANAGER`, and `AGENT` roles:

- `POST /clubs/{club_id}/wallet/send-out`
- `POST /clubs/{club_id}/wallet/claim-back`

Agents can only send chips to or claim chips back from members assigned to them through `agent_id`. Both flows write one idempotent ledger record to `clubs/{club_id}/transactions/{transaction_id}`. Filter the ledger by `type` to show `SEND_OUT` or `CLAIM_BACK`; do not create duplicate agent-specific transfer collections.

Current backend Firestore send-out document sample (not the API response):

```json
{
  "transaction_id": "00000000-0000-0000-0000-000000000001",
  "currency_type": "CLUB_CHIPS",
  "type": "SEND_OUT",
  "amount": 500,
  "from_user_id": "agent-firebase-uid",
  "to_user_id": "member-firebase-uid",
  "performed_by": "agent-firebase-uid",
  "note": "Club chip deposit",
  "timestamp": "Firestore server timestamp"
}
```

The current writer stores `timestamp` and `performed_by`; these are existing storage fields, not the proposed canonical `created_at`/`created_by` names. The send-out API response does not include those timestamp/actor fields. Do not rename or duplicate them in Firestore from the UI. If a future API exposes the raw event, normalize its timestamp/actor in the adapter only when their exact semantics are confirmed.

For `CLAIM_BACK`, `from_user_id` is the member and `to_user_id` / `performed_by` is the operator. The current API and ledger use Firebase UIDs in these fields; switching business transfer references to `player_id` requires a coordinated backend/API migration.

The proposed `club_wallet_transfers`, `club_wallet_sendouts`, and `club_wallet_claimbacks` collections are not current collections and should not be created as UI-owned parallel ledgers. Revisit a unified wallet ledger only as part of the planned wallet/account backend redesign.

### Planned table lifecycle additions
- `table_escrow/{table_id}`
- `table_lifecycle/{table_id}`
- `table_settlements/{table_id}/{hand_id}`

Purpose:
- separate table stack/escrow from member wallets
- support close/settlement lifecycle and hand summaries

### Planned local room / friend table additions
- `local_rooms/{room_id}`
- `friend_tables/{table_id}`
- `friend_table_invites/{invite_id}`

Purpose:
- support local preview and friend-table flows in the UI contract

## Safe update rule for the UI team

The UI may add new names, types, and collections as long as they are treated as:
- canonical frontend fields
- mapping aliases where necessary
- future storage additions that are not yet required in the repo

The UI must not create parallel fields like:
- `member_balance` and `chip_balance` both as independent live state values in the same screen unless one is explicitly an alias or a transformation
- `player_id` and `user_id` as duplicate active identities when only one is needed
- `trade_id` and `transaction_id` as separate authoritative ledger IDs without a defined mapping rule

## UI field availability

Use these fields when returned by the current endpoints/snapshots:

- Membership: `club_id`, `user_id` (Firebase UID), `username`, `role`
- Wallet summary: `club_id`, `user_id`, `role`, `currency_type`, `available_balance`, `in_play_balance`
- Table seats/snapshots: `table_id`, `user_id`, `username`, `seat_index`, `stack`, `status`, `hand_id`, `pot_total`
- Ledger transfer response: `transaction_id`, `type`, `amount`, `from_user_id`, `to_user_id`, `status`

These remain planned until the backend exposes them in API responses: `player_id`, `club_uuid`, `agent_player_id`, `nickname`, `member_status`, Diamond wallet fields (`wallet_id`, `wallet_type`, `diamond_balance`), club star/plan/capacity fields, and moderation/GPS/VIP fields. `club_uuid` becomes usable only when the migrated API returns it alongside the new 8-digit `club_id`; until then, treat the current UUID-valued `club_id` as opaque. Do not make planned fields required or read them directly from Firestore as a shortcut.

## No-break rule for current storage

The live Firebase/Firestore documents in the repo should remain the source of truth until the backend explicitly updates the schema. The UI should not assume a direct 1:1 field rename is safe.

The UI contract is a compatibility layer, not a forced migration.

## Summary for the UI team

The backend is not missing a complete world of collections; it is missing a clean field contract and a clearer wallet lifecycle model.

The practical plan is:
- standardize names at the UI layer
- map legacy storage fields to canonical names
- add only the missing collections/fields that are truly needed by the new wallet and lifecycle contract
- keep runtime/internal data out of the UI sync layer

This keeps the frontend work organized without breaking the live repo state.

---

## 1) Platform wallet collections

### 1.1 `platform_wallets/global-playmoney-reserve`

Sample:

```json
{
  "wallet_id": "global-playmoney-reserve",
  "wallet_type": "PLATFORM_RESERVE",
  "currency_type": "GLOBAL_PLAYMONEY",
  "chip_balance": 999980000,
  "initial_funding": 1000000000
}
```

Fields:
- `wallet_id`
- `wallet_type`
- `currency_type`
- `chip_balance`
- `initial_funding`

UI use:
- Internal only; not user-facing in the normal app flow.

### 1.2 `platform_wallets/global-playmoney-reserve/transactions/{transaction_id}`

Sample:

```json
{
  "transaction_id": "demo-player-a-grant",
  "type": "NEW_PLAYER_GRANT",
  "currency_type": "GLOBAL_PLAYMONEY",
  "amount": 20000,
  "from_wallet_id": "global-playmoney-reserve",
  "to_wallet_id": "global-wallet-demo-player-a",
  "performed_by": "system",
  "balance_after": 999980000
}
```

Fields:
- `transaction_id`
- `type`
- `currency_type`
- `amount`
- `from_wallet_id`
- `to_wallet_id`
- `performed_by`
- `balance_after`

UI use:
- Debug/logging only.

---

## 2) User global wallet collections

### 2.1 `global_wallets/{user_id}`

Sample:

```json
{
  "wallet_id": "global-wallet-demo-player-a",
  "wallet_type": "GLOBAL_PLAYMONEY",
  "currency_type": "GLOBAL_PLAYMONEY",
  "owner_user_id": "demo-player-a",
  "chip_balance": 20000
}
```

Fields:
- `wallet_id`
- `wallet_type`
- `currency_type`
- `owner_user_id`
- `chip_balance`

UI use:
- Show as the landing page / global play-money wallet.
- Do not combine with club chips.

### 2.2 `global_wallets/{user_id}/transactions/{transaction_id}`

Sample:

```json
{
  "transaction_id": "initial-grant",
  "type": "NEW_PLAYER_GRANT",
  "currency_type": "GLOBAL_PLAYMONEY",
  "amount": 20000,
  "from_wallet_id": "global-playmoney-reserve",
  "to_user_id": "demo-player-a",
  "performed_by": "system"
}
```

Fields:
- `transaction_id`
- `type`
- `currency_type`
- `amount`
- `from_wallet_id`
- `to_user_id`
- `performed_by`

UI use:
- Debug/logging only.

---

## 3) Club member and wallet collections

### 3.1 `clubs/{club_id}`

Sample:

```json
{
  "club_id": "demo-club",
  "name": "Demo Club",
  "status": "active",
  "owner_user_id": "demo-owner",
  "created_by": "demo-platform-admin"
}
```

Fields:
- `club_id`
- `name`
- `status`
- `owner_user_id`
- `created_by`

UI use:
- Club identity and selected club context.

### 3.2 `clubs/{club_id}/members/{user_id}`

Current demo-fixture sample; the demo IDs below are placeholders, not Firebase UIDs:

```json
{
  "user_id": "demo-player-a",
  "username": "Demo Player A",
  "role": "PLAYER",
  "status": "active",
  "chip_balance": 14600,
  "in_play_balance": 600
}
```

Fields:
- `user_id`
- `username`
- `role`
- `status`
- `chip_balance`
- `in_play_balance`

UI use:
- Most important club member record for the app.
- Use `role` for UI visibility and permission hints.
- Current API returns `username`; `nickname` support is planned, not implemented.
- Current wallet API maps stored `chip_balance` to `available_balance` and returns `in_play_balance` separately.

### 3.3 `clubs/{club_id}/members/{user_id}/transactions/{buy_in_id}`

Sample:

```json
{
  "transaction_id": "demo-buy-in-a",
  "type": "buy_in",
  "currency_type": "CLUB_CHIPS",
  "table_id": "demo-club-table",
  "seat_index": 0,
  "amount": 500,
  "chip_balance_delta": -500,
  "in_play_balance_delta": 500
}
```

Fields:
- `transaction_id`
- `type`
- `currency_type`
- `table_id`
- `seat_index`
- `amount`
- `chip_balance_delta`
- `in_play_balance_delta`

UI use:
- Useful for buy-in/audit log display.
- Not a primary game state source.

### 3.4 `clubs/{club_id}/transactions/{transaction_id}`

Sample:

```json
{
  "transaction_id": "demo-buy-in-a",
  "type": "BUY_IN",
  "currency_type": "CLUB_CHIPS",
  "from_user_id": "demo-player-a",
  "to_table_id": "demo-club-table",
  "amount": 500
}
```

Fields:
- `transaction_id`
- `type`
- `currency_type`
- `amount`
- `from_user_id`
- `to_user_id`
- `to_club_id`
- `to_table_id`
- `performed_by`

UI use:
- Audit/history display.
- Not needed to play the table.

### 3.5 `clubs/{club_id}/treasury/wallet`

Sample:

```json
{
  "chip_balance": 10000
}
```

Fields:
- `chip_balance`

UI use:
- Owner/manager treasury display only.

### 3.6 `clubs/{club_id}/treasury/pools`

Sample:

```json
{
  "total_rake_collected": 0,
  "bad_beat_jackpot_pool": 0,
  "hand_of_the_day_pool": 0
}
```

Fields:
- `total_rake_collected`
- `bad_beat_jackpot_pool`
- `hand_of_the_day_pool`

UI use:
- Advanced/owner-only display; not required for basic game flows.

---

## 4) Table and seat collections

### 4.1 `clubs/{club_id}/tables/{table_id}`

Sample:

```json
{
  "table_id": "demo-club-table",
  "club_id": "demo-club",
  "game_id": "no_limit_texas_holdem",
  "status": "demo_archive"
}
```

Fields:
- `table_id`
- `club_id`
- `game_id`
- `status`

UI use:
- Lobby and table list rendering.

### 4.2 `clubs/{club_id}/tables/{table_id}/seats/{seat_index}`

Current demo-fixture sample:

```json
{
  "seat_index": 0,
  "user_id": "demo-player-a",
  "username": "Demo Player A",
  "stack": 600,
  "buy_in_id": "demo-buy-in-a",
  "status": "demo_archive"
}
```

Fields:
- `seat_index`
- `user_id`
- `username`
- `stack`
- `buy_in_id`
- `status`

UI use:
- Seat assignment and stack display.

### 4.3 `clubs/{club_id}/tables/{table_id}/hands/{hand_id}`

Current demo-fixture audit sample. Backend-only; use the replay API for UI history because it filters private card data.

```json
{
  "hand_id": "demo-hand",
  "club_id": "demo-club",
  "table_id": "demo-club-table",
  "settlement_status": "settled",
  "pot_total": 200,
  "players": [
    {
      "user_id": "demo-player-a",
      "seat": 0,
      "starting_stack": 500,
      "final_stack": 600,
      "net_delta": 100
    },
    {
      "user_id": "demo-player-b",
      "seat": 1,
      "starting_stack": 500,
      "final_stack": 400,
      "net_delta": -100
    }
  ]
}
```

Fields:
- `hand_id`
- `club_id`
- `table_id`
- `started_at`
- `timestamp`
- `final_board`
- `board`
- `pot_total`
- `rake_amount`
- `settlement_status`
- `is_showdown`
- `winners`
- `uncalled_returns`
- `deck_state`
- `frames`
- `players`

Note:
- This raw hand document currently contains an embedded player array and backend-only replay/audit data; it is not a UI payload.
- Current seat identity is `user_id`; `player_id` and `nickname` require future backend support.

UI use:
- Replay history and settlement summary.
- Never read raw private hand data directly; use the replay API instead.

---

## 5) Runtime durability and ownership collections

These are backend-internal and are present when Firestore is enabled.

### 5.1 `clubs/{club_id}/tables/{table_id}/runtime/state`

Sample:

```json
{
  "schema_version": 1,
  "scope_type": "club",
  "scope_id": "demo-club",
  "table_id": "table-1",
  "owner_id": "worker-a",
  "fencing_epoch": 1,
  "sequence": 42,
  "checkpoint": { "table": { "table_id": "table-1" } },
  "updated_at": "Firestore server timestamp"
}
```

Fields:
- `schema_version`
- `scope_type`
- `scope_id`
- `table_id`
- `owner_id`
- `fencing_epoch`
- `sequence`
- `checkpoint`
- `updated_at`

UI use:
- None. Backend only.

### 5.2 `clubs/{club_id}/tables/{table_id}/runtime/state/actions/{action_hash}`

Sample:

```json
{
  "user_id": "player-a",
  "action_id": "action-123",
  "payload_hash": "abc123...",
  "sequence": 43,
  "fencing_epoch": 1,
  "committed_at": "Firestore server timestamp"
}
```

Fields:
- `user_id`
- `action_id`
- `payload_hash`
- `sequence`
- `fencing_epoch`
- `committed_at`

UI use:
- None. Backend only.

### 5.3 `clubs/{club_id}/tables/{table_id}/runtime/lease`

Sample:

```json
{
  "table_id": "table-1",
  "owner_id": "worker-a",
  "fencing_generation": 1,
  "lease_expires_at": "2026-10-18T12:00:00Z",
  "heartbeat_at": "2026-10-18T11:59:00Z",
  "state_version": 42,
  "last_action_seq": 17,
  "status": "active"
}
```

Fields:
- `table_id`
- `owner_id`
- `fencing_generation`
- `lease_expires_at`
- `heartbeat_at`
- `state_version`
- `last_action_seq`
- `status`

UI use:
- None. Backend only.

---

## 6) Union collections

### 6.1 `unions/{union_id}`

Sample:

```json
{
  "union_id": "demo-union",
  "name": "Demo Union",
  "status": "active",
  "created_by": "demo-owner"
}
```

Fields:
- `union_id`
- `name`
- `status`
- `created_by`

UI use:
- Shared lobby and union table list.

### 6.2 `unions/{union_id}/admins/{user_id}`

Sample:

```json
{
  "user_id": "demo-owner",
  "role": "UNION_ADMIN",
  "status": "active"
}
```

Fields:
- `user_id`
- `role`
- `status`

UI use:
- UI can display union-admin-only controls, but permission is server-authoritative.

### 6.3 `unions/{union_id}/clubs/{club_id}`

Sample:

```json
{
  "union_id": "demo-union",
  "club_id": "demo-club",
  "union_name": "Demo Union",
  "status": "active"
}
```

Fields:
- `union_id`
- `club_id`
- `union_name`
- `status`

UI use:
- Determine if a club is participating in a union table lobby.

### 6.4 `unions/{union_id}/tables/{table_id}`

Sample:

```json
{
  "union_id": "demo-union",
  "table_id": "demo-union-table",
  "origin_club_id": "demo-club",
  "game_id": "no_limit_texas_holdem",
  "status": "demo_archive"
}
```

Fields:
- `union_id`
- `table_id`
- `origin_club_id`
- `game_id`
- `status`

UI use:
- Shared table list and route selection.

### 6.5 `unions/{union_id}/tables/{table_id}/seats/{seat_index}`

Sample:

```json
{
  "seat_index": 0,
  "user_id": "demo-player-a",
  "club_id": "demo-club",
  "stack": 100,
  "status": "demo_archive"
}
```

Fields:
- `seat_index`
- `user_id`
- `club_id`
- `stack`
- `status`

UI use:
- Union seat display; display the player's home `club_id`.

### 6.6 `unions/{union_id}/tables/{table_id}/hands/{hand_id}`

Sample:

```json
{
  "union_id": "demo-union",
  "table_id": "demo-union-table",
  "hand_id": "demo-union-hand",
  "settlement_status": "settled",
  "pot_total": 0,
  "players": [
    {"user_id": "demo-player-a", "seat": 0, "final_stack": 100, "net_delta": 0},
    {"user_id": "demo-player-b", "seat": 1, "final_stack": 100, "net_delta": 0}
  ]
}
```

Fields:
- `union_id`
- `table_id`
- `hand_id`
- `settlement_status`
- `pot_total`
- `players`

UI use:
- Shared-hand settlement display.

### 6.7 `unions/{union_id}/settlements/{YYYY-MM-DD}`

Sample:

```json
{
  "union_id": "demo-union",
  "settlement_date": "2026-10-17"
}
```

Fields:
- `union_id`
- `settlement_date`

### 6.8 `unions/{union_id}/settlements/{YYYY-MM-DD}/clubs/{club_id}`

Sample:

```json
{
  "club_id": "demo-club",
  "debt": 0,
  "credit": 0,
  "net_delta": 0
}
```

Fields:
- `club_id`
- `debt`
- `credit`
- `net_delta`

### 6.9 `unions/{union_id}/settlements/{YYYY-MM-DD}/hands/{hand_id}`

Sample:

```json
{
  "hand_id": "demo-union-hand",
  "table_id": "demo-union-table",
  "positions": {
    "demo-club": {"net_delta": 0, "debt": 0, "credit": 0}
  },
  "rake_amount": 0
}
```

Fields:
- `hand_id`
- `table_id`
- `positions`
- `rake_amount`

UI use:
- Optional admin/settlement reporting only.

---

## 7) The UI fields to log and display first

These are safe, UI-facing fields for rendering and client diagnostics when returned by the API/snapshot:

- `club_id`
- `table_id`
- `seat_index`
- `user_id`
- `username`
- `role`
- `game_id`
- `status`
- `available_balance` (wallet API only)
- `in_play_balance`
- `stack`
- `buy_in_id`
- `hand_id`
- `settlement_status`
- `pot_total`
- `union_id`
- `club_id` on union seats and union hand players

Do not log or display runtime ownership fields such as `owner_id` or `fencing_generation`; they are backend-internal. Do not read raw Firestore `chip_balance` for UI diagnostics; use the API's `available_balance`.

---

## 8) Fields to avoid exposing in the UI

These are raw backend internals and must not be sent directly to client screens:

- full `deck_state`
- private hole cards in raw audit docs
- `runtime/state` checkpoint payloads
- `runtime/lease` ownership state
- raw action journal entries
- unmasked opponent cards in hand audits
- internal `payload_hash` data
- any document under `.../runtime/...`

Use the replay API instead of reading the raw `hands/{hand_id}` audit tables directly.

---

## 9) Practical frontend checklist

- Read `GET /clubs/{club_id}/members/me` for current member `role` and `user_id`.
- Read `GET /clubs/{club_id}/members/me/wallet` for `available_balance` and `in_play_balance`.
- Read `GET /me/wallets/global-playmoney` for the global wallet.
- Read club and union tables from the REST API, not from Firestore.
- For gameplay, use live socket snapshots and the replay API; do not inspect raw Firestore hand docs.
- Log only safe, non-sensitive fields.

This is the minimum UI contract that matches the current backend model.
