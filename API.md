# API and WebSocket Contract

This guide describes the endpoints and messages currently implemented for a UI client. Protected REST routes require a Firebase ID token; table sockets require it in an initial authentication message. Club and union membership are verified server-side. The intended economy keeps club-managed money balances separate from global non-cash play-money balances; only some club-chip flows are implemented here, and the global wallet/table flows are not yet present. This is an initial authorization layer, not a complete production security review.

Realtime gameplay is websocket-based. The app protocol for table state and player actions is the WebSocket connection; it is not gRPC. The gRPC dependency is only part of the Google Cloud Firebase/Firestore client stack used for auth and optional persistence.

Base URL for local development: `http://127.0.0.1:8000`  
WebSocket base URL: `ws://127.0.0.1:8000`

FastAPI also provides interactive route documentation at `/docs` while the server is running.

## UI Integration Flow

Use the signed-in Firebase user's ID token for every protected REST request and for each new table WebSocket connection. The backend derives `user_id` and role from that verified token and the active Firestore membership; do not send a user ID in the socket URL or use the UI's selected role as authorization.

1. Call `GET /clubs/{club_id}/members/me` to load the server-assigned club role and display name.
2. Call `GET /clubs/{club_id}/tables` and `GET /clubs/{club_id}/unions` to populate the separate Club Tables and Union Tables lobby tabs.
3. For a shared lobby, call `GET /clubs/{club_id}/unions/{union_id}/tables`. A club must be an active member of that union.
4. As `OWNER` or `MANAGER`, create a local table with `POST /clubs/{club_id}/tables/{table_id}`. Only `UNION_ADMIN` can create/start/delete union tables.
5. As `PLAYER`, buy in locally with `POST /clubs/{club_id}/tables/{table_id}/seats`, or into a union table with `POST /unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats`. The union seat request debits the selected home club's member balance. Generate one UUID for `buy_in_id`; retain and reuse it only when retrying that same purchase.
6. Connect to `/ws/table/{club_id}/{table_id}` for a local table, or `/ws/union/{union_id}/{table_id}` for a shared table. Send the Firebase token first; union auth also includes the player's verified home-club selection. Wait for `authenticated` and then render the player-specific snapshot.
7. Start local hands with the club endpoint or shared hands with the union endpoint. Send game actions over the socket and replace the UI state with each complete snapshot.
8. Use the snapshot's `hand_id` for replay. Local hands use `/clubs/{club_id}/hands/{hand_id}/replay`; union hands use `/unions/{union_id}/hands/{hand_id}/replay?club_id={club_id}`.

The Angular client reads Firebase configuration from `window.__COWBOYS_FIREBASE_CONFIG__`; the host must provide that configuration and establish a signed-in user before protected API calls can succeed. The demo `player-a` ID alone cannot authenticate. Local CORS defaults allow `http://localhost:4200` and `http://127.0.0.1:4200`. Set `CORS_ALLOWED_ORIGINS` to a comma-separated exact-origin allowlist in each deployment.

## Club Roles and Current Membership

Membership roles are `OWNER` (Club owner), `MANAGER` (Manager), `AGENT` (Agent), and `PLAYER` (Player). The role is assigned by the club and is read-only in the client. Use the endpoint below to populate a role panel; for example, show “Your current role is PLAYER. Role assignment is managed by the club.” Do not use a client-selected role for authorization.

| Role | Current backend access |
| --- | --- |
| `OWNER` | Create tables, start hands, and manage club wallet transfers. |
| `MANAGER` | Create tables, start hands, and manage club wallet transfers. |
| `AGENT` | Send chips to and claim chips back from assigned downline members, subject to the agent's available balance. |
| `PLAYER` | Buy in, take a seat, and connect to play. |

All roles require active membership. Role assignment is not exposed by this API.

`UNION_ADMIN` is a separate union-scoped authority, not a club role. It is assigned under `unions/{union_id}/admins/{user_id}` and does not grant extra privileges over the user's club. See [Union Tables and Settlement](#union-tables-and-settlement).

### Get my membership

`GET /clubs/{club_id}/members/me`

Requires a valid Firebase ID token and active membership. Response `200`:

```json
{
  "club_id": "demo",
  "user_id": "player-a",
  "username": "joppy2024",
  "role": "PLAYER"
}
```

The `role` value is authoritative and is always one of the four role IDs listed above.

Invalid/missing REST credentials return `401`; a valid Firebase user who is not an active club member returns `403`.

### Platform club provisioning

`POST /platform/clubs` requires a valid Firebase ID token with the trusted custom claim `platform_admin: true`. The claim must be assigned through a trusted Firebase Admin process; this API does not allow users to grant themselves platform-admin access.

Request:

```json
{
  "name": "New Club",
  "owner_user_id": "firebase-user-id"
}
```

The endpoint returns `201` with the generated club ID, name, owner ID, and initial treasury balance:

```json
{
  "club_id": "generated-club-id",
  "name": "New Club",
  "owner_user_id": "firebase-user-id",
  "treasury_balance": 10000
}
```

It creates an active `OWNER` membership for `owner_user_id`, initializes the shared treasury wallet with exactly 10,000 `CLUB_CHIPS`, and writes an `INITIAL_GRANT` audit entry atomically. The grant is not added to the owner's personal balance. Existing clubs are not funded by this endpoint. The API currently does not verify that `owner_user_id` belongs to an existing Firebase Auth user, so the platform admin must supply the correct UID. Club IDs are generated for each request and there is no request idempotency key; a retry after an ambiguous timeout can create another club and another grant. Do not blindly retry a request whose outcome is unknown.

### Club wallet transfers

`POST /clubs/{club_id}/wallet/send-out` transfers available club chips from the authenticated `OWNER`, `MANAGER`, or `AGENT` to a member. An `AGENT` may only transfer to a member whose `agent_id` is the agent's user ID. `POST /clubs/{club_id}/wallet/claim-back` transfers available chips from a member back to the authenticated operator; claim-back is rejected while the member has a nonzero `in_play_balance`. Both operations require a unique `transaction_id` UUID and write balanced member updates plus a club-wide audit record in one Firestore transaction. Reuse the same ID only to safely retry the same transfer.

Request:

```json
{
  "user_id": "player-a",
  "amount": 500,
  "transaction_id": "00000000-0000-0000-0000-000000000001",
  "note": "Club chip deposit"
}
```

Both endpoints return the transfer type, amount, transaction ID, and server-derived source/destination IDs. Insufficient balance, unauthorized downline, in-play funds, and reused IDs with different transfer details return `409` or `403` as appropriate. Club wallet routes only manage private club chips; global play-money tables and persistent global-wallet APIs are not implemented.

### Club treasury transfers

`GET /clubs/{club_id}/wallet` returns the shared club treasury balance. Only active `OWNER` and `MANAGER` members can read it. The balance is separate from member balances and rake/jackpot pools.

`POST /clubs/{club_id}/wallet/transfers` sends chips from the source club's treasury wallet to another existing club's treasury wallet. Only an active `OWNER` or `MANAGER` of the source club can authorize the transfer; the receiving club does not need to approve it. The source must have sufficient treasury funds. Both club balances and paired audit records are updated atomically, and a repeated `transaction_id` safely replays the same transfer.

Request:

```json
{
  "destination_club_id": "club-b",
  "amount": 2500,
  "transaction_id": "00000000-0000-0000-0000-000000000002",
  "note": "Inter-club funding"
}
```

Card, debit, credit, and PayPal purchases are not enabled yet. A payment integration must verify provider settlement before crediting a club treasury.

## Hand History Replay

`GET /clubs/{club_id}/hands/{hand_id}/replay`

Requires a valid Firebase ID token and active membership in the requested club. Missing/invalid authentication and inactive/non-member accounts return `403`. A missing hand returns `404`; unavailable Firebase/Firestore services return `503`.

The replay response contains timestamped frames with the board, pot, actor, last action, stacks, and player cards. Card visibility is enforced by the server: the requesting player's cards are visible to them; opponents' cards are visible only when the hand reached showdown and that player stayed in the hand without mucking. Opponent cards in all other cases are `["??", "??"]`. Raw audit operations and deck state are never included in this response.

```json
{
  "hand_id": "hand-1",
  "timestamp": "2026-10-14T10:00:00+00:00",
  "final_board": ["As", "3h", "5d"],
  "pot_total": 450,
  "is_showdown": false,
  "winners": [{"user_id": "player-a", "amount": 450}],
  "total_frames": 1,
  "frames": [
    {
      "frame_index": 0,
      "timestamp": "2026-10-14T10:00:00+00:00",
      "street": 0,
      "community_cards": [],
      "pot": 150,
      "active_seat": 1,
      "last_action": null,
      "players": [
        {"user_id": "player-a", "username": "Alice", "seat": 0, "stack": 450, "mucked": false, "in_hand": true, "hole_cards": ["As", "Ad"]},
        {"user_id": "player-b", "username": "Bob", "seat": 1, "stack": 400, "mucked": false, "in_hand": false, "hole_cards": ["??", "??"]}
      ]
    }
  ]
}
```

Union hand replay uses `GET /unions/{union_id}/hands/{hand_id}/replay?club_id={club_id}`. It requires an active member of a club linked to that union and applies the same player-specific card filtering.

## Union Tables and Settlement

Union membership is provisioned in Firestore; there are no create-union, join-union, or invite endpoints yet. Required documents:

```text
unions/{union_id}
  admins/{user_id}             {"role":"UNION_ADMIN","status":"active"}
  clubs/{club_id}              {"union_id":"union-1","club_id":"club-a","union_name":"Alliance","status":"active"}
  tables/{table_id}            Union table configuration and status
    seats/{seat_index}         Home club, user, and current stack
    hands/{hand_id}            Restricted complete audit and replay frames
  settlements/{YYYY-MM-DD}
    clubs/{club_id}            Accumulated debt and credit for that date
    hands/{hand_id}            Idempotency record and per-club positions
```

An active admin document with role `UNION_ADMIN` is required for union table creation, starting, and deletion. A club `OWNER` or `MANAGER` does not inherit this authority. A player must have an active `PLAYER` membership in the selected club, and that club must have an active link under the union.

### Discover shared tables

`GET /clubs/{club_id}/unions` returns the active unions linked to the caller's active club. `GET /clubs/{club_id}/unions/{union_id}/tables` returns open in-process union tables, with the table name, origin club, blinds, buy-in bounds, seat count, and running state. Local tables are listed by `GET /clubs/{club_id}/tables`.

### Create and delete a shared table

`POST /unions/{union_id}/tables/{table_id}` requires `UNION_ADMIN`.

```json
{
  "origin_club_id": "club-a",
  "table_name": "NLH 50/100 Union Main",
  "small_blind": 50,
  "big_blind": 100,
  "buy_in_min": 500,
  "buy_in_max": 5000
}
```

The origin club must be an active union member. The route creates metadata at `unions/{union_id}/tables/{table_id}` and starts an in-process game. `DELETE /unions/{union_id}/tables/{table_id}` is also `UNION_ADMIN`-only and only closes an empty table; it returns `409` if a hand is running or players remain seated so funds cannot be orphaned. Closed table documents are retained as audit metadata.

### Seat and play

`POST /unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats` requires an active `PLAYER` membership in the path club and an active union-club link. Its body is the same as the local seat request (`seat_index`, `buy_in_amount`, `buy_in_id`); it does not accept a user ID or stack. The authenticated UID and username come from Firebase/member data. The Firestore transaction debits that member's home-club `chip_balance`, increments its `in_play_balance`, and reserves the seat under the union table.

`POST /unions/{union_id}/tables/{table_id}/hands` requires `UNION_ADMIN`. Shared WebSockets connect to:

```text
ws://127.0.0.1:8000/ws/union/{union_id}/{table_id}
```

Their first message includes the selected home club so the server can verify both identities:

```json
{"type":"authenticate","id_token":"<Firebase ID token>","club_id":"club-a"}
```

The table snapshot includes `is_union_table`, `union_id`, `origin_club_id`, and `table_name`; each player entry includes its verified `club_id`. Show that club tag next to each player in the shared table UI. The server still sends private cards only according to the existing player masking rules.

```typescript
const socket = new WebSocket(
  `${wsBase}/ws/union/${encodeURIComponent(unionId)}/${encodeURIComponent(tableId)}`
);
socket.addEventListener('open', () => {
  socket.send(JSON.stringify({
    type: 'authenticate',
    id_token: idToken,
    club_id: homeClubId
  }));
});
```

### Inter-club clearing

On settlement, each member's `in_play_balance` receives their own hand `net_delta`. The same Firestore transaction records a per-hand clearing entry and increments daily club positions. A club with a negative sum of player deltas accrues debt `-net_delta`; a club with a positive sum accrues credit `net_delta`. For example, if Club A's player loses 500 to Club B's player, A debt increases by 500 and B credit increases by 500. Per-hand idempotency prevents a retry from applying either member balances or clearing positions twice.

The engine currently charges no rake (`rake_amount` is zero). No Union software fee or traffic-based rake split is applied; configure a rake rate and allocation policy before enabling that financial feature.

For REST calls, send `Authorization: Bearer <Firebase ID token>`. In the browser, get the token for the signed-in Firebase user; never send the Firebase web API key as an access token.

```javascript
import { getAuth } from "firebase/auth";

const user = getAuth(app).currentUser;
if (!user) throw new Error("Sign in first");
const idToken = await user.getIdToken();

const response = await fetch(`${apiBase}/clubs/${clubId}/tables/${tableId}`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${idToken}`,
  },
  body: JSON.stringify(tableConfig),
});
```

## REST Endpoints

### Health

`GET /health`

Response `200`:

```json
{"status":"ok"}
```

### Create a table

`POST /clubs/{club_id}/tables/{table_id}`

Requires an active club member with role `OWNER` or `MANAGER`.

Request:

```json
{
  "small_blind": 50,
  "big_blind": 100,
  "buy_in_min": 100,
  "buy_in_max": 1000
}
```

Response `201`:

```json
{
  "club_id": "demo",
  "table_id": "table-1",
  "status": "created"
}
```

Blinds and buy-in limits must be positive, the big blind must not be less than the small blind, and the maximum buy-in must not be less than the minimum. The table is currently created in process memory.

### Update table financial settings

`PATCH /clubs/{club_id}/tables/{table_id}/settings` requires `OWNER` or `MANAGER`. The union equivalent requires `UNION_ADMIN`. `rake_rate` accepts values from `0` through `0.1` (including `0.075`); `rake_cap` and `rake_min_pot` are integer chip amounts. `no_flop_no_drop` disables rake when the hand ends before a flop. BBJ fee collection honors `jackpot_min_pot`. On local club tables, actual rake and BBJ drops reduce winner stacks and update the club treasury in the same idempotent settlement transaction.

BBJ qualifying-hand evaluation/payout, Hand-of-the-Day funding and awards, and union fee allocation are not implemented. Global play-money wallets and global tables are also not implemented by this API.

### Seat a player

`POST /clubs/{club_id}/tables/{table_id}/seats`

The authenticated member may only buy in their own account. The server gets their UID and username from verified identity/member data; clients must not send `user_id` or `username`.

Request:

```json
{
  "seat_index": 0,
  "buy_in_amount": 500,
  "buy_in_id": "00000000-0000-0000-0000-000000000001"
}
```

`buy_in_amount` is the requested amount, not an assigned stack. The server checks the table's buy-in limits and the member's Firestore `chip_balance`, then atomically debits that balance and reserves the seat. `buy_in_id` is a client-generated UUID idempotency key; generate it once for a purchase and reuse the same value if retrying after a timeout or network error. Do not generate a new key for a retry.

Response `201`: a table-state snapshot. Before a hand starts, `hand_id` is `null`, `is_hand_running` is `false`, and the seated player has an empty `hole_cards` array. The server assigns `stack` equal to the verified `buy_in_amount`.

### Start a hand

`POST /clubs/{club_id}/tables/{table_id}/hands`

Requires an active club member with role `OWNER` or `MANAGER`. No request body. Response `201`: a table-state snapshot. This REST response intentionally does not include private cards; connect to the WebSocket as each player to receive that player's private cards.

## WebSocket

Connect each seated player to:

```text
ws://127.0.0.1:8000/ws/table/{club_id}/{table_id}
```

The path contains only `club_id` and `table_id`. Do not append a `user_id`; the server obtains the user ID from the verified Firebase token.

On `open`, send the Firebase ID token before any game action:

```json
{"type":"authenticate","id_token":"<Firebase ID token>"}
```

In Angular/TypeScript, send authentication on `open`, then distinguish the authentication acknowledgement, a complete table snapshot, and an action error:

```typescript
const wsBase = apiBase.replace(/^http/, 'ws').replace(/\/$/, '');
const socket = new WebSocket(
  `${wsBase}/ws/table/${encodeURIComponent(clubId)}/${encodeURIComponent(tableId)}`
);

socket.addEventListener('open', () => {
  socket.send(JSON.stringify({ type: 'authenticate', id_token: idToken }));
});

socket.addEventListener('message', (event: MessageEvent<string>) => {
  const message: unknown = JSON.parse(event.data);
  if (typeof message === 'object' && message !== null && 'type' in message
      && message.type === 'authenticated') {
    // Authentication succeeded; the next message is the initial table snapshot.
    return;
  }
  if (typeof message === 'object' && message !== null && 'error' in message) {
    showActionError(String(message.error));
    return;
  }
  replaceTableState(message as TableSnapshot);
});
```

The Firebase token must be sent within 10 seconds after the socket opens. The server verifies the token, checks active club membership, requires role `PLAYER`, and confirms that the token's UID is seated. It then replies:

```json
{"type":"authenticated","user_id":"firebase-uid"}
```

The next message is a complete player-specific state snapshot. After each accepted action or automatic timeout, the server broadcasts a fresh complete snapshot to each connected player; each player's own cards are visible only on their connection. An invalid action sends `{"error":"<human-readable message>"}` to the acting socket and does not close it. A reconnect must use a new WebSocket, send authentication again, and wait for a fresh snapshot. The server does not currently send heartbeat messages; rely on the browser's `close`/`error` events and reconnect with a fresh ID token.

### Versioned envelope protocol

The legacy message shape above remains supported. Clients can opt into envelope version `1.0` by using the envelope for their first authentication message; the server uses that version for the rest of that socket session. The server derives the expected topic from the WebSocket route and rejects a client topic that does not match it. Do not use the topic as authorization or send a caller-selected user ID.

Client messages include `version`, the exact route-derived `topic`, `event`, a non-empty `correlation_id` of at most 128 characters, and an object `payload`. Client timestamps are not used; server responses include a UTC `timestamp`.

Authentication:

```json
{
  "version": "1.0",
  "topic": "clubs/demo/tables/table-1/game",
  "event": "AUTHENTICATE",
  "correlation_id": "auth-1",
  "payload": {"id_token": "<Firebase ID token>"}
}
```

For union sockets, use topic `unions/{union_id}/tables/{table_id}/game` and include the selected `club_id` in the authentication payload. After authentication, the server sends `AUTHENTICATED` and `TABLE_SNAPSHOT` envelopes. Each envelope contains `version`, `topic`, `event`, `correlation_id`, `timestamp`, and `payload`. Snapshots remain complete states and are masked for the receiving player.

Game action:

```json
{
  "version": "1.0",
  "topic": "clubs/demo/tables/table-1/game",
  "event": "PLAYER_ACTION",
  "correlation_id": "action-1",
  "payload": {"action": "RAISE", "amount": 300}
}
```

Queued pre-actions use `event: "PRE_ACTION"` and `payload: {"action": "CHECK_FOLD"}`; send `null` to clear a queued action. The server responds with `PRE_ACTION_SET`. Invalid commands receive `ACTION_ERROR` with `payload: {"message": "..."}` and the request's correlation ID when valid. A successful game action is reflected in the next `TABLE_SNAPSHOT`; snapshots are broadcasts and have a null correlation ID. Event topics and timestamps on server messages are generated by the server, not copied from client input.

WebSocket close codes:

| Code | Meaning |
| --- | --- |
| `4401` | Missing/malformed authentication message or invalid token. |
| `4403` | Inactive membership, unavailable membership record, non-`PLAYER` role, or user is not seated. |
| `4404` | Table does not exist in this server process. |
| `4406` | Unsupported versioned WebSocket protocol version. |
| `4408` | Authentication was not received within 10 seconds. |
| `1013` | Firebase or Firestore service is unavailable. |

Example snapshot sent to `player-a`:

```json
{
  "table_id": "table-1",
  "table_name": "Main Table",
  "is_union_table": false,
  "union_id": null,
  "origin_club_id": "demo",
  "hand_id": "bfb74a70-1dad-4d8a-bf76-e192d23b50ee",
  "is_hand_running": true,
  "street": 0,
  "community_cards": [],
  "pot": 150,
  "pots": [],
  "active_turn_seat": 0,
  "players": [
    {
      "seat": 0,
      "user_id": "player-a",
      "username": "Alice",
      "club_id": "demo",
      "stack": 450,
      "status": "sitting_in",
      "hole_cards": ["As", "Ad"],
      "in_hand": true
    },
    {
      "seat": 1,
      "user_id": "player-b",
      "username": "Bob",
      "club_id": "demo",
      "stack": 400,
      "status": "sitting_in",
      "hole_cards": ["??", "??"],
      "in_hand": true
    }
  ]
}
```

`street` is PokerKit's numeric street index (`0` is pre-flop for the current Hold'em game; `null` means there is no active street). `pot` is the current pot including outstanding bets. `pots` contains the current pot/side-pot amounts. `active_turn_seat` is the seat index to act, or `null` when nobody is acting. Opponents' hidden cards are represented as `"??"`; a player's own cards are sent in clear text only to that player's socket. `hand_id` remains available after completion and can be used to load the replay.

### Client actions

While another player is acting, a seated player may queue one pre-action on their WebSocket:

```json
{"type":"pre_action","action":"CHECK_FOLD"}
{"type":"pre_action","action":"FOLD"}
{"type":"pre_action","action":"CALL_ANY"}
```

Send `{"type":"pre_action","action":null}` to clear it. The server acknowledges with `pre_action_set`; the player's own snapshots include their `pre_action` value. On that player's turn, `CHECK_FOLD` checks if no call is due and folds otherwise, `FOLD` folds, and `CALL_ANY` calls the current amount or checks if none is due. The queued action is cleared on execution or when a new hand starts. The server applies queued actions under the table lock and broadcasts the resulting snapshot like a normal action.

Send one JSON object per action:

```json
{"action":"FOLD"}
{"action":"CHECK"}
{"action":"CALL"}
{"action":"RAISE","amount":300}
```

Actions are case-insensitive. `RAISE.amount` is an integer raise-to total for the current betting round, not the additional amount to add. PokerKit validates whether the action and amount are legal. `CHECK` is rejected if the player faces a bet. Only the current actor may act.

On success, all connected players receive the next full state snapshot; there is no separate action acknowledgement. On invalid input or an illegal/out-of-turn action, only the acting socket receives:

```json
{"error":"It is not this player's turn."}
```

The error value is a human-readable string and should not be used as a stable machine code. The configured turn timeout automatically checks when no bet is faced and folds when facing a bet; its result is broadcast as another full snapshot.

## HTTP Errors

| Status | Current cases |
| --- | --- |
| `401` | Missing/invalid credentials on standard protected REST routes; invalid/expired/revoked token. |
| `403` | Inactive/non-member account, invalid role, or insufficient route permission. The replay endpoint intentionally returns `403` for missing/invalid authentication too. |
| `404` | Unknown table, hand, or club member account. |
| `409` | Duplicate table, occupied/invalid seat, insufficient balance, conflicting `buy_in_id`, hand cannot start, or a non-empty union table cannot be deleted. |
| `422` | Request body fails validation or table blind/buy-in configuration is invalid. |
| `503` | Firebase token verification or Firestore is unavailable. For a possibly timed-out buy-in, retry with the same `buy_in_id`. |

## UI Integration Notes

- Create a local or union table and seat players before opening their table sockets; start a hand after at least two players are seated.
- Treat WebSocket state as the source for rendering the live table. It is a full snapshot, not a sequence of incremental events.
- Use `active_turn_seat` and the current user's seat to determine whether to show controls. The server remains authoritative even if the UI hides controls.
- Use the signed-in Firebase user's ID token for REST and WebSocket authentication. The server derives the UID from the verified token; do not send a caller-selected `user_id`.
- Open local sockets at `/ws/table/{club_id}/{table_id}` and union sockets at `/ws/union/{union_id}/{table_id}`; send `{ type: 'authenticate', id_token }` immediately on `open`, adding `club_id` for union sockets. Do not put a user ID or token in the URL.
- Load completed replays through the matching club or union replay endpoint; never read raw Firestore hand documents from a client.
- Use a new UUID `buy_in_id` for each intentional purchase and reuse it only when retrying that purchase. The server checks funds and applies the debit; the client must not submit a chip stack.
- Table lists cover only tables running in the current process. There are still no sit-out, leave-seat, top-up, cash-out, union formation/invitation, or rake-splitting routes. Initial buy-in is performed by the seat endpoint; game actions are sent over WebSocket.
- State is held in memory. A process restart or routing a socket to another Cloud Run instance can lose or split a live table; session affinity is not durable storage.