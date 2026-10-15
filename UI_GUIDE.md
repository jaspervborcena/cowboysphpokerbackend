# UI Integration Guide

This guide describes how a browser UI should connect to the Cowboys PH Poker backend. It covers the implemented club and union table workflows, REST authentication, WebSocket v1 messages, state rendering, errors, and reconnect behavior. Detailed endpoint fields and response schemas remain in [API.md](API.md); the protocol rationale and scaling limits are in [websocket.md](websocket.md).

## Before Starting

- Configure Firebase in the host page as `window.__COWBOYS_FIREBASE_CONFIG__` and sign the user in with Firebase Authentication.
- Run the API using the local setup in [README.md](README.md). Protected workflows require Firebase verification and active membership data in Firestore.
- Configure the UI's API base URL, for example `http://127.0.0.1:8000`. Build the WebSocket URL by replacing `http` with `ws` and preserving the same host and port.
- The UI source is not in this backend repository. This guide is the integration contract for the Angular client or another browser client.

## Session and Permissions

1. Get the signed-in Firebase user's ID token when making a request. Send it on protected REST calls as `Authorization: Bearer <id_token>`.
2. Call `GET /clubs/{club_id}/members/me` and use the returned `role`, `username`, and `user_id` to render the session and available controls.
3. Treat the role as read-only. The server authorizes every operation from the verified Firebase identity and active membership; never trust a role, user ID, or permission supplied by the UI.
4. On `40101`, refresh/re-authenticate with Firebase and retry a safe read. On `40301`, show an access-denied state and do not repeatedly retry.

Roles currently used by the UI:

| Role | UI actions currently allowed |
| --- | --- |
| `OWNER`, `MANAGER` | Create local tables, start local hands, manage permitted club wallet operations. |
| `AGENT` | Manage permitted transfers to and from assigned downline members. |
| `PLAYER` | Buy in, take a seat, and play. |
| `UNION_ADMIN` | Create, start, or delete eligible shared union tables. This is union-scoped, not a club role. |

## Lobby and Table Flow

For a club lobby, load `GET /clubs/{club_id}/tables` and `GET /clubs/{club_id}/unions`. For a selected shared lobby, load `GET /clubs/{club_id}/unions/{union_id}/tables`. Keep local club tables and shared union tables distinct in the UI.

For a local table:

1. An `OWNER` or `MANAGER` creates it with `POST /clubs/{club_id}/tables/{table_id}`. Send `small_blind`, `big_blind`, `buy_in_min`, and `buy_in_max`.
2. A `PLAYER` chooses a seat and buy-in amount. Call `POST /clubs/{club_id}/tables/{table_id}/seats` with `seat_index`, `buy_in_amount`, and a new UUID `buy_in_id`.
3. Keep that `buy_in_id` for the lifetime of the purchase attempt. If the request times out, retry the same request with the same ID; do not debit or assign chips locally.
4. Connect each seated player's client to `/ws/table/{club_id}/{table_id}` and authenticate it as described below.
5. An `OWNER` or `MANAGER` starts a hand through `POST /clubs/{club_id}/tables/{table_id}/hands`. Render gameplay from the WebSocket snapshot, not the REST start response.

For a union table, an active `UNION_ADMIN` creates and starts it through the union endpoints. Each player buys in through `POST /unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats` using their own active home club, then connects to `/ws/union/{union_id}/{table_id}` and includes that home `club_id` in authentication. The union table is shared; players do not connect to their clubs' separate local tables. Use each snapshot player's `club_id` when displaying club affiliation.

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
  "topic": "clubs/demo/tables/table-1/game",
  "event": "AUTHENTICATE",
  "correlation_id": "auth-<uuid>",
  "payload": {"id_token": "<Firebase ID token>"}
}
```

For union sockets, include `"club_id": "<player home club>"` in `payload`. On success, expect `AUTHENTICATED`, then `TABLE_SNAPSHOT`. Initial authentication failures return a standardized error body and close the socket. The server verifies the Firebase token, active membership, `PLAYER` role, selected home club for union play, and seat before registering the connection.

Every `TABLE_SNAPSHOT` payload is a complete state. Replace the UI's current table state with it; do not merge it as a partial update. The server masks opponents' private cards per recipient. Render `"??"` as hidden cards and never infer or reveal another player's cards. The REST hand-start response does not contain player-private cards.

## Player Actions and Pre-Actions

Send each intentional game action as `PLAYER_ACTION`. `correlation_id` identifies the message/response pair; `action_id` is the idempotency key for the actual poker action. Generate a new UUID for each new action, and reuse the same `action_id` and exact payload only when retrying that action.

```json
{
  "version": "1.0",
  "topic": "clubs/demo/tables/table-1/game",
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
  "topic": "clubs/demo/tables/table-1/game",
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

- Open `http://127.0.0.1:8000/docs` to inspect REST routes while the API is running.
- Verify missing/invalid Firebase credentials render the generic `40101` response.
- Verify local and union players see different masked snapshots and that union player rows show `club_id`.
- Verify duplicate action retries do not apply a second game action, and that the client answers server PINGs.
- Treat Cloud Run multi-worker deployment, durable live-hand recovery, chat, and global tables as unsupported until their documented work is implemented.