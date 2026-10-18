# UI Integration Guide

This guide describes how a browser UI should connect to the Cowboys PH Poker services. It covers wallet REST calls, club and union table workflows, Firebase authentication, WebSocket v1 messages, state rendering, errors, and reconnect behavior. Detailed endpoint fields and response schemas remain in [API.md](API.md); the protocol rationale and scaling limits are in [websocket.md](websocket.md).

## Before Starting

- Configure Firebase in the host page as `window.__COWBOYS_FIREBASE_CONFIG__` and sign the user in with Firebase Authentication.
- Send the current Firebase ID token as `Authorization: Bearer <id_token>` on protected REST requests; do not access wallet documents directly from Firestore.
- Configure separate service URLs. The deployed Wallet API is `https://cowboys-wallet-api-4rusyqoipa-de.a.run.app`; local development can run it at `http://127.0.0.1:8001` using [README.md](README.md). The game server's REST and WebSocket base remains independently configured (local REST example: `http://127.0.0.1:8000`; WebSocket uses `ws://127.0.0.1:8000`).
- Use the Wallet API base URL for membership and wallet endpoints. Use the game-server base URL for live tables, gameplay REST routes, and WebSockets. The wallet API currently allows browser origins `http://localhost:4200` and `http://127.0.0.1:4200`.
- The UI source is not in this backend repository. This guide is the integration contract for the Angular client or another browser client.

## UI Work Can Start Now

The game-engine rewrite does not block the current frontend. The UI can implement the existing Firebase sign-in, membership/role display, club and union lobbies, NLH table creation, buy-in/seat flow, player-specific WebSocket snapshots, actions, pre-actions, wallet views, safe errors, and hand replay. Use the current endpoint and message examples in this guide and [API.md](API.md).

Keep poker behavior server-authoritative: render the supplied snapshot and legal controls, but do not calculate winners, mutate chip balances, deal cards, or infer hidden cards in the client. The current supported create choice is `no_limit_texas_holdem`; use `/games` and only offer entries with `create_supported: true`.

Frontend work can proceed against typed API/WebSocket interfaces and fixture-backed local mocks. Keep transport, DTOs, and screen state separate so engine internals can change without rewriting the UI.

## UI Implementation Checklist

### Session and lobby

- [ ] Implement Firebase sign-in, token refresh, sign-out, and loading/error states. Send the Firebase ID token to protected REST routes and as the first WebSocket authentication message; never trust a client-selected UID or role.
- [ ] Load `GET /clubs/{club_id}/members/me` and display the server-assigned role/profile. Use the role only to guide UI visibility; the server remains authoritative for permissions.
- [ ] Build club and union lobby views from `GET /clubs/{club_id}/tables`, `GET /clubs/{club_id}/unions`, `GET /clubs/{club_id}/unions/{union_id}/tables`, and `GET /games`, including loading, empty, retry, and error states.
- [ ] Build club and union lobby views from `GET /clubs/{club_id}/tables`, `GET /clubs/{club_id}/unions`, `GET /clubs/{club_id}/unions/{union_id}/tables`, and `GET /games`, including loading, empty, retry, and error states. Union tables appear only for active union member clubs.
- [ ] Add the two-step union flow: club owner/manager looks up the union by letter, requests representative approval, then requests club membership after approval; union admins review and approve both queues.

### Wallets and funding

- [ ] After sign-in, call `POST /me/wallets/global-playmoney/ensure` without a client-selected amount, then load `GET /me/wallets/global-playmoney`. Label it non-cash play money; global-play-money tables and transfers to club chips are not supported.
- [ ] Load `GET /clubs/{club_id}/members/me/wallet` and show `available_balance` separately from `in_play_balance`. Load the separate club treasury through `GET /clubs/{club_id}/wallet` only for authorized owner/manager screens.
- [ ] Add owner/manager or assigned-agent send-out and claim-back UI using the existing wallet routes and transaction UUIDs. Display server results as authoritative; explain that claim-back is unavailable while the member has chips in play.
- [ ] Do not present separate agent and player balances: the backend currently has one member balance pair per club membership. Keep composite wallet fixtures marked as mock.
- [ ] Keep `POST /platform/clubs` in a trusted platform-admin bootstrap surface only; it creates a real club and reserve-funded treasury, not a normal self-service club request.
- [ ] A player cash-out request may appear only as clearly labeled pending/mock UI until a request/approval API exists. Do not change balances locally.

### Tables and gameplay

- [ ] Let active `OWNER`/`MANAGER` members create tables using `/games` entries marked `create_supported: true`; currently only NLH is supported.
- [ ] For authorized table-settings controls, call `PATCH /clubs/{club_id}/tables/{table_id}/settings` as `OWNER`/`MANAGER` or `PATCH /unions/{union_id}/tables/{table_id}/settings` as `UNION_ADMIN`. Include a fresh `settings_update_id` UUID and reuse it with the identical settings only when retrying that update.
- [ ] For authorized table-settings controls, call `PATCH /clubs/{club_id}/tables/{table_id}/settings` as `OWNER`/`MANAGER` or the union equivalent as an `OWNER`/`MANAGER` of the table's origin club. Include a fresh `settings_update_id` UUID and reuse it with the identical settings only when retrying that update.
- [ ] Let a `PLAYER` buy in and choose a seat. Generate one `buy_in_id` per purchase and reuse it with the exact same request on retries; never debit or assign chips optimistically.
- [ ] Implement the v1 WebSocket flow: authenticate, render complete player-specific snapshots, send actions with stable `action_id`s, support pre-actions and PING/PONG, map numeric errors, and replace local state with each full snapshot.
- [ ] Add hand replay through the club/union replay APIs. Do not read raw Firestore audit data or expose hidden cards.
- [ ] Support union table discovery, home-club selection, seat purchase, and union socket authentication for active participating members. Show each verified home club in table snapshots.
- [ ] Expose union table create/start/delete only to `UNION_ADMIN`; rely on the server to reject deletion when a table is not empty.
- [ ] Create union-linked tables through `POST /clubs/{club_id}/tables/{table_id}` with an approved `union_id`; use the union's theme and show its code in the title. Origin-club owners/managers start or delete those tables; union admins approve representatives and clubs, but do not automatically control other clubs' tables.

### Acceptance checks

- [ ] Verify flows with owner/manager, player, agent, and union-admin accounts; cover auth expiry, retry IDs, rejected permissions, two-club union play, per-player card masking, reconnect snapshots, and wallet refresh.
- [ ] Do not call `/workers/{worker_id}/...` from the client. Those routes are internal, unauthenticated, process-local, and not a supported UI contract.
- [ ] Keep the client engine-agnostic: the server owns rules, outcomes, balances, and card visibility. Coordinate/version any breaking contract changes before rollout.

### Wallet API

The Wallet API is deployed separately from the game server. After Firebase sign-in, call `POST /me/wallets/global-playmoney/ensure` once to provision the signed-in user's wallet, then call `GET /me/wallets/global-playmoney` on later loads. The ensure endpoint idempotently grants 20,000 chips from the platform reserve. Do not simulate or modify wallet balances in the UI.

Use distinct labels and never add balances from different scopes together:

| Surface | Wallet to show | UI behavior |
| --- | --- | --- |
| Landing page | `GLOBAL_PLAYMONEY` | Load from `GET /me/wallets/global-playmoney`; provision with the `ensure` endpoint if needed. This wallet is separate from club chips. |
| Selected club, player view | Current member balance | Load `GET /clubs/{club_id}/members/me/wallet`. Use the returned `club_uuid` in `{club_id}` paths; the Firestore membership record is at `clubs/{club_uuid}/members/{uid}`. Show `available_balance` and `in_play_balance` separately; render the current table stack from the game snapshot. |
| Selected club, agent tools | Current member balance | No separate `CLUB_AGENT` account exists. `POST /clubs/{club_id}/wallet/send-out` and `/claim-back` support authorized transfers against the same member balance. |
| Selected club, owner/manager tools | `CLUB_TREASURY` | `GET /clubs/{club_id}/wallet` reads the treasury. `POST /platform/clubs` creates a club with 10,000 treasury chips; the owner's personal member balance starts at zero. |
| Active table | `TABLE_ESCROW` / seat stack | Show the player's stack for this table; do not treat it as another spendable wallet. |

All wallet routes require a Firebase ID token. Club routes additionally require an active club membership; `GET /clubs/{club_id}/wallet` and inter-club treasury transfers require `OWNER` or `MANAGER`, while member send-out and claim-back permit `OWNER`, `MANAGER`, or an assigned `AGENT`. Claim-back is rejected while the member's `in_play_balance` is nonzero. Transfer requests require an idempotency `transaction_id`. `POST /platform/clubs` is a real platform-admin mutation that creates a club and reserve-funded treasury; it is not a non-authoritative proposal or draft flow.

The backend does not provide a player-initiated cash-out request/approval workflow. A UI may show a clearly labeled pending mock request, but must not change balances locally. An authorized operator can fulfill a claim-back through the existing endpoint. `GET /clubs/{club_id}/members/me` returns role/profile data; use the `/wallet` route for member balances.

The same person may have both agent and player roles, but current balances are not separate by role. The wallet-dashboard fixture remains a conceptual composite, not a single API response; label its data as mock and use the live routes for real balances and club-chip movements.

### Membership and club provisioning API

Use the Wallet API base URL and send `Authorization: Bearer <id_token>` on both requests. Do not send a user ID for membership lookup; the server derives it from the verified token.

| Operation | Request and parameters | Authorization | Response / behavior |
| --- | --- | --- | --- |
| Get my membership | `GET /clubs/{club_id}/members/me`; path `club_id` is currently the club UUID (`club_uuid`), despite the route parameter name. No query parameters or body. | Active club membership. | `200`: `club_id` (8-digit business ID), `club_uuid`, Firebase UID `user_id`, 8-digit `player_id`, `username`, and authoritative `role`. `401` for invalid credentials; `403` if not an active member; `404` if the club is missing. |
| Create a club | `POST /platform/clubs`; no path or query parameters. JSON body: `name` (1-100 characters) and `owner_player_id` (exactly 8 digits). | Firebase ID token with trusted `platform_admin: true` claim. | `201`: generated `club_id` (8-digit business ID), `club_uuid`, `name`, resolved `owner_user_id` (Firebase UID), `owner_player_id`, and `treasury_balance` (10,000). `404` if the owner player ID is unknown; `403` if caller is not a platform admin. This is a trusted bootstrap operation, not a player-facing create-club flow. |

For club REST paths that currently take `{club_id}`, pass the UUID returned as `club_uuid`, not the 8-digit business `club_id`. The server returns both identifiers so the UI can keep operational routing and public display/search identities distinct. Platform provisioning has no idempotency key; do not blindly retry after an ambiguous timeout.

Proposed UI mock shape (not a current API response):

```typescript
type WalletType =
  | 'GLOBAL_PLAYMONEY'
  | 'CLUB_TREASURY'
  | 'CLUB_AGENT'
  | 'CLUB_PLAYER'
  | 'TABLE_ESCROW';

interface WalletSummary {
  wallet_id: string;
  wallet_type: WalletType;
  currency_type: 'GLOBAL_PLAYMONEY' | 'CLUB_CHIPS';
  balance: number;
  club_id: string | null;
  owner_user_id: string | null;
}
```

Use [`ui-mocks/wallet-dashboard.json`](ui-mocks/wallet-dashboard.json) as the local fixture for these surfaces. It deliberately represents one user with both `AGENT` and `PLAYER` roles and distinct wallet IDs. The demo `Cowboys Global Room` treasury is a mock `CLUB_TREASURY` with 1,000,000,000 `CLUB_CHIPS`; it is not the user's `GLOBAL_PLAYMONEY` wallet, a live club, or a balance returned by the current API. The `mock_only` marker is intentional: these values are not returned by a live endpoint. `in_play_balance` includes the listed table stack; do not add the table stack to the player's available balance or show it as spendable funds. Keep global play chips visually and functionally separate from all club-chip accounts.

## Contract Through Engine Replacement

The backend's live NLH path uses the first-party engine and preserves the REST routes, `game_id`, standardized errors, v1 WebSocket envelopes, NLH action payloads, complete `TABLE_SNAPSHOT` behavior, per-player card masking, and hand replay route. New seat/start fields are optional. Version any future breaking protocol change and coordinate it before rollout.

Treat `game_id`, `display_name`, and `create_supported` as the UI-facing game catalog fields. Do not couple UI code to engine event records or state objects. Never read raw Firestore hand audit documents from the client.

## Wait For Backend Support

- Do not offer PLO/PLO5/PLO6, Tongits, or any other game until its first-party engine is create-supported and its actions, snapshots, replay, and settlement behavior are documented.
- Tournament registration, blind-level controls, eliminations, table balancing, and tournament prize views need a separately implemented tournament API and event contract.
- Sit-out/sit-in, leave-seat, rebuy/top-up, and cash-out controls should wait for their server endpoints and idempotency rules.
- Reconnect by `last_event_id`, state-version conflict handling, and cross-worker action routing are future protocols. Today, reconnect by authenticating again and replacing UI state with the full snapshot.
- Do not call the game server's `/workers/{worker_id}/...` coordination routes from the client. They require an internal bearer token and remain process-local rather than a production worker control plane.
- Do not assume that multi-worker table recovery, durable action deduplication, chat, or 1,000-10,000 player capacity is implemented.
- Reserve-backed initial grants for global wallets and club treasuries are implemented. Platform reserve top-ups, game-earned global chips, and global-play-money tables are not implemented. Do not display a club treasury or club-member balance as the global wallet; user balances remain separate and cannot be converted between wallet types.

## Session and Permissions

1. Get the signed-in Firebase user's ID token when making a request. Send it on protected REST calls as `Authorization: Bearer <id_token>`.
2. Call `GET /clubs/{club_id}/members/me` on the Wallet API using the club UUID in the current path parameter. Use the returned `role`, `username`, `user_id`, `player_id`, `club_id`, and `club_uuid` to render the session and identify the club. Load member balances separately from `GET /clubs/{club_id}/members/me/wallet`.
3. Treat the role as read-only. The server authorizes every operation from the verified Firebase identity and active membership; never trust a role, user ID, or permission supplied by the UI.
4. On `40101`, refresh/re-authenticate with Firebase and retry a safe read. On `40301`, show an access-denied state and do not repeatedly retry.

Roles currently used by the UI:

| Role | UI actions currently allowed |
| --- | --- |
| `OWNER`, `MANAGER` | Create local tables, start local hands, manage permitted club wallet operations. |
| `AGENT` | Manage permitted transfers to and from assigned downline members. |
| `PLAYER` | Buy in, take a seat, and play. |
| `UNION_ADMIN` | Approve union representatives and club membership requests. This is union-scoped, not a club role. |

## Lobby and Table Flow

For a club lobby, load `GET /clubs/{club_id}/tables` and `GET /clubs/{club_id}/unions`. For a selected shared lobby, load `GET /clubs/{club_id}/unions/{union_id}/tables`. Keep local club tables and shared union tables distinct in the UI.

Load `GET /games` to inspect available variants. Use `active_game_id` as the default and only allow table creation with entries marked `create_supported: true`. Include the selected `game_id` in create requests; it defaults to `no_limit_texas_holdem` when omitted. Currently NLH is the only create-supported game. Table listings, create responses, and WebSocket snapshots include `game_id`.

For a local table:

1. An `OWNER` or `MANAGER` creates it with `POST /clubs/{club_id}/tables/{table_id}`. Send `game_id`, `small_blind`, `big_blind`, `buy_in_min`, `buy_in_max`, and `table_size` from the UI template.
2. A `PLAYER` chooses a seat and buy-in amount. Call `POST /clubs/{club_id}/tables/{table_id}/seats` with `seat_index`, `buy_in_amount`, and a new UUID `buy_in_id`.
3. Keep that `buy_in_id` for the lifetime of the purchase attempt. If the request times out, retry the same request with the same ID; do not debit or assign chips locally.
4. Connect each seated player's client to `/ws/table/{club_id}/{table_id}` and authenticate it as described below.
5. An `OWNER` or `MANAGER` starts a hand through `POST /clubs/{club_id}/tables/{table_id}/hands`. Render gameplay from the WebSocket snapshot, not the REST start response.

For a union table, the origin club's active `OWNER`/`MANAGER` creates it through `POST /clubs/{club_id}/tables/{table_id}` with `union_id` set to an approved union. The union code and theme come from the union; the owner/manager can choose the table name. Each player buys in through `POST /unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats` using their own active home club, then connects to `/ws/union/{union_id}/{table_id}` and includes that home `club_id` in authentication. Players in any active member club can see the table. Use each snapshot player's `club_id` when displaying club affiliation.

`table_size` sets the capacity for local and union tables. It must be at least `2` and cannot exceed the backend's `MAX_SEATS` setting (default `9`). If omitted by an older client, the server uses `MAX_SEATS`. Mode, fees, restrictions, and other advanced template settings are not yet supported by the create endpoints. Catalog entries not marked create-supported must not be offered as playable variants.

## WebSocket v1

Use protocol version `1.0` for new clients. The topic must match the socket route exactly:

```text
Local: clubs/{club_id}/tables/{table_id}/game
Union: unions/{union_id}/tables/{table_id}/game
```

Send the authentication envelope as the first frame after `open`. Do not put the token or a caller-selected user ID in the URL.

```json
{
  "version": "1.0",
  "topic": "clubs/550e8400-e29b-41d4-a716-446655440000/tables/table-1/game",
  "event": "AUTHENTICATE",
  "correlation_id": "auth-<uuid>",
  "payload": {"id_token": "<Firebase ID token>"}
}
```

For union sockets, include `"club_id": "<player home club UUID>"` in `payload`; this field name is retained by the protocol, but its current value must be `club_uuid`, not the 8-digit business ID. On success, expect `AUTHENTICATED`, then `TABLE_SNAPSHOT`. Initial authentication failures return a standardized error body and close the socket. The server verifies the Firebase token, active membership, `PLAYER` role, selected home club for union play, and seat before registering the connection.

Every `TABLE_SNAPSHOT` payload is a complete state. Replace the UI's current table state with it; do not merge it as a partial update. The server masks opponents' private cards per recipient. Render `"??"` as hidden cards and never infer or reveal another player's cards. The REST hand-start response does not contain player-private cards.

## Player Actions and Pre-Actions

Send each intentional game action as `PLAYER_ACTION`. `correlation_id` identifies the message/response pair; `action_id` is the idempotency key for the actual poker action. Generate a new UUID for each new action, and reuse the same `action_id` and exact payload only when retrying that action.

```json
{
  "version": "1.0",
  "topic": "clubs/550e8400-e29b-41d4-a716-446655440000/tables/table-1/game",
  "event": "PLAYER_ACTION",
  "correlation_id": "message-<uuid>",
  "payload": {"action": "RAISE", "amount": 300, "action_id": "action-<uuid>"}
}
```

Supported actions are `FOLD`, `CHECK`, `CALL`, and `RAISE`. Raise `amount` is the total to raise to in the current betting round, not the amount to add. The server validates the actor, turn, and legal sizing. A successful action is reflected in a new full snapshot. A duplicate retry may produce a refreshed snapshot and `ACTION_ACKNOWLEDGED` with `duplicate: true`. Reusing an action ID with different details returns code `40902`; do not generate a new ID to conceal that conflict.

To queue a pre-action while waiting for a turn, send `PRE_ACTION` with `action` set to `CHECK_FOLD`, `FOLD`, or `CALL_ANY`. Use `null` to clear it. The server acknowledges with `PRE_ACTION_SET`; show the player's selected pre-action from their next snapshots.

The current deduplication cache is process-local and bounded. It does not protect retries across server restarts, cache eviction, or multiple workers. The server is not ready for multi-worker live tables; see the recovery design in [websocket.md](websocket.md).

## Heartbeats and Reconnects

The server sends a `PING` periodically. Reply with `PONG` using the same `correlation_id`, exact topic, version, and an empty object payload:

```json
{
  "version": "1.0",
  "topic": "clubs/550e8400-e29b-41d4-a716-446655440000/tables/table-1/game",
  "event": "PONG",
  "correlation_id": "<correlation_id from PING>",
  "payload": {}
}
```

The default heartbeat interval is 30 seconds and the default idle timeout is 90 seconds; deployment environment variables can change these values. If the connection closes, reconnect with a fresh Firebase token, authenticate again, and replace local state with the new full snapshot. Avoid replaying an uncertain action with a new ID. The backend currently has no multi-worker table recovery, snapshot sequence number, or durable action journal.

## Errors

All HTTP and WebSocket errors use numeric codes and generic client-safe text. Match on `code`, not message wording. A valid v1 socket action error is an `ACTION_ERROR` envelope whose `payload` contains `status`, `code`, `message`, `errors`, and `trace_id`. Errors during initial authentication and malformed JSON may arrive as the error body directly before the socket closes. Show a friendly message and retain `trace_id` for support; never display technical exception details.

| Code | Suggested UI behavior |
| --- | --- |
| `40001`, `40002` | Highlight invalid or missing input; do not retry unchanged. |
| `40101` | Refresh Firebase sign-in, then reconnect or retry a safe request. |
| `40301` | Show access denied; refresh membership/role state. |
| `40401` | Show that the table or requested item is unavailable. |
| `40901` | Refresh the relevant table or balance state before another attempt. |
| `40902` | Treat as an action-ID conflict; do not repeat with altered details. |
| `42901` | Wait before retrying. |
| `50001`, `50002` | Show a generic temporary failure and offer a controlled retry. |

For an uncertain buy-in result, retry with the original `buy_in_id`. For an uncertain game action, keep the original `action_id` and exact payload while the same server process is available; this is not durable across restart or worker change.

## Useful Checks

- Open `https://cowboys-wallet-api-4rusyqoipa-de.a.run.app/docs` for the deployed wallet REST routes, or `http://127.0.0.1:8001/docs` for a local wallet API. The game server's local REST docs remain at `http://127.0.0.1:8000/docs`.
- Verify missing/invalid Firebase credentials render the generic `40101` response.
- Verify local and union players see different masked snapshots and that union player rows show `club_id`.
- Verify duplicate action retries do not apply a second game action, and that the client answers server PINGs.
- Treat Cloud Run multi-worker deployment, durable live-hand recovery, chat, and global tables as unsupported until their documented work is implemented.