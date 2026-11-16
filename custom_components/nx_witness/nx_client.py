"""NX Witness REST API v4 Client."""
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class NXWitnessClient:
    """Client for NX Witness REST API v4."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the client."""
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.session = session
        self.token: str | None = None
        # Status of the most recent request (0 = connection error / no login).
        self.last_status: int = 0

    @property
    def reachable(self) -> bool:
        """Whether the last request reached the server."""
        return self.last_status != 0

    async def login(self) -> bool:
        """Login and obtain session token."""
        url = f"{self.host}/rest/v4/login/sessions"
        try:
            async with self.session.post(
                url,
                json={"username": self.username, "password": self.password},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (200, 201):
                    result = await response.json()
                    self.token = result.get("token")
                    self.last_status = response.status
                    _LOGGER.info("Login to %s successful, token obtained", self.host)
                    return True
                self.last_status = response.status
                text = await response.text()
                _LOGGER.error("Login failed with status %s: %s", response.status, text)
                return False
        except (aiohttp.ClientError, TimeoutError) as ex:
            self.last_status = 0
            _LOGGER.error("Login to %s failed: %s", self.host, ex)
            return False

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[int, Any]:
        """Make an authenticated request, refreshing the token once on 401."""
        for attempt in range(2):
            if not self.token and not await self.login():
                return self.last_status, None
            headers = {"Authorization": f"Bearer {self.token}"}
            try:
                async with self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                    **kwargs,
                ) as response:
                    self.last_status = response.status
                    if response.status == 401 and attempt == 0:
                        _LOGGER.debug("Token expired, re-logging in")
                        self.token = None
                        continue
                    if response.status in (200, 201):
                        return response.status, await response.json()
                    _LOGGER.error(
                        "%s %s returned %s", method.upper(), url, response.status
                    )
                    return response.status, None
            except (aiohttp.ClientError, TimeoutError) as ex:
                self.last_status = 0
                _LOGGER.error("Request error %s %s: %s", method.upper(), url, ex)
                return 0, None
        return 0, None

    async def get_ticket(self, force_new_token: bool = False) -> str | None:
        """Get a one-time ticket for media streaming."""
        if force_new_token:
            self.token = None
        url = f"{self.host}/rest/v4/login/tickets"
        _, result = await self._request("post", url)
        if isinstance(result, dict):
            ticket = result.get("token")
            _LOGGER.debug("Ticket obtained")
            return ticket
        return None

    async def get_cameras(self) -> list[dict[str, Any]] | None:
        """Get list of cameras from NX Witness, or None if the request failed."""
        url = f"{self.host}/rest/v4/devices"
        _, data = await self._request("get", url)
        if not isinstance(data, list):
            return None
        cameras = [
            {
                "id": device.get("id"),
                "name": device.get("name"),
                "model": device.get("model"),
                "status": device.get("status"),
                # Owning mediaserver; changes when a camera is moved.
                "server_id": device.get("serverId") or device.get("parentId"),
            }
            for device in data
            if device.get("deviceType") == "Camera"
        ]
        _LOGGER.debug("Found %d cameras", len(cameras))
        return cameras

    async def get_servers(self) -> list[dict[str, Any]] | None:
        """Get the mediaservers in this system, or None if the request failed."""
        url = f"{self.host}/rest/v4/servers"
        _, data = await self._request("get", url)
        if not isinstance(data, list):
            return None
        return [
            {
                "id": server.get("id"),
                "name": server.get("name"),
                "url": server.get("url"),
                "endpoints": server.get("endpoints") or [],
                "status": server.get("status"),
            }
            for server in data
            if isinstance(server, dict) and server.get("id")
        ]

    def get_camera_stream_url(self, camera_id: str, ticket: str) -> str:
        """Get stream URL for a camera with ticket."""
        return f"{self.host}/rest/v4/devices/{camera_id}/media?_ticket={ticket}"

    def get_camera_image_url(self, camera_id: str, ticket: str) -> str:
        """Get snapshot URL for a camera with ticket."""
        return f"{self.host}/rest/v4/devices/{camera_id}/image?_ticket={ticket}"

    async def get_event_log(
        self,
        start_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get events from NX Witness event log."""
        url = f"{self.host}/rest/v4/events/log"
        params: dict[str, int] = {}
        if start_time_ms is not None:
            params["startTimeMs"] = start_time_ms
        _, data = await self._request("get", url, params=params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "events", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []
