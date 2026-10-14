# API and WebSocket Contract

This guide describes the endpoints and messages currently implemented for a UI client. REST routes require a Firebase ID token; table sockets require the same token in an initial authentication message. The backend verifies tokens against the configured Firebase project and checks active club membership. This is an initial authorization layer, not a complete production security review.

Base URL for local development: `http://127.0.0.1:8000`  
WebSocket base URL: `ws://127.0.0.1:8000`

FastAPI also provides interactive route documentation at `/docs` while the server is running.

## UI Integration Flow

Use the signed-in Firebase user's ID token for every protected REST request and for each new table WebSocket connection. The backend derives `user_id` and role from that verified token and the active Firestore membership; do not send a user ID in the socket URL or use the UI's selected role as authorization.

1. Call `GET /clubs/{club_id}/members/me` to load the server-assigned role and display name.
2. As `OWNER` or `MANAGER`, create the table with `POST /clubs/{club_id}/tables/{table_id}`. Players do not create tables.
3. As `PLAYER`, reserve a seat with `POST /clubs/{club_id}/tables/{table_id}/seats`. Generate one UUID for `buy_in_id`; retain and reuse it only if retrying that same request.
4. Open `ws://{host}/ws/table/{club_id}/{table_id}` and send the Firebase ID token as the first WebSocket message. Wait for the `authenticated` acknowledgement and then render the player-specific initial snapshot.
5. As `OWNER` or `MANAGER`, start the hand with `POST /clubs/{club_id}/tables/{table_id}/hands`. The REST response is not a private player snapshot; render each player's cards from that player's authenticated WebSocket instead.
6. Send game actions over the WebSocket. Treat every subsequent snapshot as the complete current state, not as a patch.
7. When a hand completes, keep its `hand_id` from the last snapshot and load its replay from `GET /clubs/{club_id}/hands/{hand_id}/replay` with the same Firebase bearer token.

The frontend currently has no Firebase sign-in/provider implementation. It must obtain a real ID token from the host Firebase Auth flow and pass that token to protected API calls; the demo `player-a` ID alone cannot authenticate. The backend also does not currently configure browser CORS. Before calling it from a different origin such as `http://localhost:4200`, configure FastAPI CORS for the exact frontend origin and required methods/headers (including `Authorization`).

## Club Roles and Current Membership

Membership roles are `OWNER` (Club owner), `MANAGER` (Manager), `AGENT` (Agent), and `PLAYER` (Player). The role is assigned by the club and is read-only in the client. Use the endpoint below to populate a role panel; for example, show “Your current role is PLAYER. Role assignment is managed by the club.” Do not use a client-selected role for authorization.

| Role | Current backend access |
| --- | --- |
| `OWNER` | Create tables and start hands. |
| `MANAGER` | Create tables and start hands. |
| `AGENT` | No role-specific API actions are currently assigned. |
| `PLAYER` | Buy in, take a seat, and connect to play. |

All roles require active membership. Role assignment is not exposed by this API.

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

WebSocket close codes:

| Code | Meaning |
| --- | --- |
| `4401` | Missing/malformed authentication message or invalid token. |
| `4403` | Inactive membership, unavailable membership record, non-`PLAYER` role, or user is not seated. |
| `4404` | Table does not exist in this server process. |
| `4408` | Authentication was not received within 10 seconds. |
| `1013` | Firebase or Firestore service is unavailable. |

Example snapshot sent to `player-a`:

```json
{
  "table_id": "table-1",
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
      "stack": 450,
      "status": "sitting_in",
      "hole_cards": ["As", "Ad"],
      "in_hand": true
    },
    {
      "seat": 1,
      "user_id": "player-b",
      "username": "Bob",
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
| `409` | Duplicate table, unavailable seat, insufficient member balance, conflicting `buy_in_id`, or hand cannot start. |
| `422` | Request body fails validation or table blind/buy-in configuration is invalid. |
| `503` | Firebase token verification or Firestore is unavailable. For a possibly timed-out buy-in, retry with the same `buy_in_id`. |

## UI Integration Notes

- Create a table and seat players before opening their table sockets; start a hand after at least two players are seated.
- Treat WebSocket state as the source for rendering the live table. It is a full snapshot, not a sequence of incremental events.
- Use `active_turn_seat` and the current user's seat to determine whether to show controls. The server remains authoritative even if the UI hides controls.
- Use the signed-in Firebase user's ID token for REST and WebSocket authentication. The server derives the UID from the verified token; do not send a caller-selected `user_id`.
- Open the WebSocket at `/ws/table/{club_id}/{table_id}` only, then send `{ type: 'authenticate', id_token }` immediately on `open`. The current UI's extra user-ID path segment and unauthenticated socket flow do not match this backend contract.
- Load completed hand replays from `GET /clubs/{club_id}/hands/{hand_id}/replay`; never read raw Firestore hand documents from a client.
- Use a new UUID `buy_in_id` for each intentional purchase and reuse it only when retrying that purchase. The server checks funds and applies the debit; the client must not submit a chip stack.
- The API currently has no table-list/get, sit-out, leave-seat, top-up, cash-out, or explicit hand-endpoint action routes. Initial buy-in is performed by the seat endpoint; game actions are sent over WebSocket.
- State is held in memory. A process restart or routing a socket to another Cloud Run instance can lose or split a live table; session affinity is not durable storage.