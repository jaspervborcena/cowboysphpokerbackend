from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket

from logic.table_game_base import TableGameBase
from logic.websocket_protocol import PROTOCOL_VERSION, game_topic, make_envelope


@dataclass
class TableConnection:
    websocket: WebSocket
    user_id: str
    protocol_version: str | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[tuple[str, str], list[TableConnection]] = {}

    async def connect(
        self,
        club_id: str,
        table_id: str,
        user_id: str,
        websocket: WebSocket,
        accepted: bool = False,
        protocol_version: str | None = None,
    ) -> None:
        if not accepted:
            await websocket.accept()
        key = (club_id, table_id)
        self.active_connections.setdefault(key, []).append(
            TableConnection(websocket, user_id, protocol_version)
        )

    def disconnect(self, club_id: str, table_id: str, websocket: WebSocket) -> None:
        key = (club_id, table_id)
        connections = self.active_connections.get(key, [])
        self.active_connections[key] = [
            connection for connection in connections if connection.websocket is not websocket
        ]
        if not self.active_connections[key]:
            self.active_connections.pop(key, None)

    def has_user(self, club_id: str, table_id: str, user_id: str) -> bool:
        return any(
            connection.user_id == user_id
            for connection in self.active_connections.get((club_id, table_id), [])
        )

    async def send_user_state(
        self,
        club_id: str,
        table_id: str,
        user_id: str,
        state: dict[str, object],
    ) -> None:
        key = (club_id, table_id)
        for connection in list(self.active_connections.get(key, [])):
            if connection.user_id != user_id:
                continue
            try:
                message = (
                    make_envelope(
                        "TABLE_SNAPSHOT",
                        state,
                        game_topic(club_id, table_id),
                    )
                    if connection.protocol_version == PROTOCOL_VERSION
                    else state
                )
                await connection.websocket.send_json(message)
            except Exception:
                self.disconnect(club_id, table_id, connection.websocket)

    async def broadcast_table_state(self, club_id: str, table_id: str, game: TableGameBase) -> None:
        key = (club_id, table_id)
        for connection in list(self.active_connections.get(key, [])):
            try:
                state = game.get_masked_state_for_player(connection.user_id)
                if connection.protocol_version == PROTOCOL_VERSION:
                    message = make_envelope(
                        "TABLE_SNAPSHOT",
                        state,
                        game_topic(club_id, table_id),
                    )
                else:
                    message = state
                await connection.websocket.send_json(message)
            except Exception:
                self.disconnect(club_id, table_id, connection.websocket)