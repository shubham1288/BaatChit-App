import logging
from typing import Dict, List

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    In-process WebSocket connection manager.

    Supports:
    - Multiple connections per user (multi-tab / multi-device)
    - Private message delivery
    - Group broadcast
    """

    def __init__(self) -> None:
        # username → [websocket, ...]
        self.active: Dict[str, List[WebSocket]] = {}

    # ─────────────────────────────────────────────
    # CONNECT / DISCONNECT
    # ─────────────────────────────────────────────

    async def connect(self, username: str, ws: WebSocket) -> None:
        self.active.setdefault(username, []).append(ws)
        logger.info("WS connected: %s (total sockets: %d)", username, len(self.active[username]))

    def disconnect(self, username: str, ws: WebSocket) -> None:
        if username in self.active:
            try:
                self.active[username].remove(ws)
            except ValueError:
                pass
            if not self.active[username]:
                del self.active[username]
        logger.info("WS disconnected: %s | online: %s", username, self.get_online())

    # ─────────────────────────────────────────────
    # SEND HELPERS
    # ─────────────────────────────────────────────

    async def send_to_user(self, username: str, data: dict) -> None:
        """Send a JSON payload to all sockets of a user."""
        dead: List[WebSocket] = []
        for ws in self.active.get(username, []):
            try:
                await ws.send_json(data)
            except Exception as exc:
                logger.warning("Send error to %s: %s", username, exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect(username, ws)

    async def broadcast_to_group(
        self, member_usernames: list[str], data: dict, exclude: str | None = None
    ) -> None:
        """Broadcast to a list of usernames, optionally excluding one."""
        for username in member_usernames:
            if username == exclude:
                continue
            await self.send_to_user(username, data)

    # ─────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────

    def is_online(self, username: str) -> bool:
        return username in self.active and bool(self.active[username])

    def get_online(self) -> list[str]:
        return list(self.active.keys())


manager = ConnectionManager()