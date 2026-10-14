from __future__ import annotations

from dataclasses import dataclass

from fastapi import WebSocket

from base_poker_game import BasePokerGame


@dataclass
class TableConnection:
    websocket: WebSocket
    user_id: str


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
    ) -> None:
        if not accepted:
            await websocket.accept()
        key = (club_id, table_id)
        self.active_connections.setdefault(key, []).append(TableConnection(websocket, user_id))

    def disconnect(self, club_id: str, table_id: str, websocket: WebSocket) -> None:
        key = (club_id, table_id)
        connections = self.active_connections.get(key, [])
        self.active_connections[key] = [
            connection for connection in connections if connection.websocket is not websocket
        ]
        if not self.active_connections[key]:
            self.active_connections.pop(key, None)

    async def broadcast_table_state(self, club_id: str, table_id: str, game: BasePokerGame) -> None:
        key = (club_id, table_id)
        for connection in list(self.active_connections.get(key, [])):
            try:
                await connection.websocket.send_json(
                    game.get_masked_state_for_player(connection.user_id)
                )
            except Exception:
                self.disconnect(club_id, table_id, connection.websocket)