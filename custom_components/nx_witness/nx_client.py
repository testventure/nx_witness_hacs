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
        self.token = None
        self.ticket = None

    async def login(self) -> bool:
        """Login and obtain session token."""
        try:
            url = f"{self.host}/rest/v4/login/sessions"
            data = {"username": self.username, "password": self.password}
            
            _LOGGER.debug("Logging in to NX Witness at %s", url)
            
            async with self.session.post(
                url,
                json=data,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in [200, 201]:  # Accept both 200 and 201
                    result = await response.json()
                    self.token = result.get("token")
                    _LOGGER.info("Login successful, token obtained")
                    return True
                _LOGGER.error("Login failed with status: %s", response.status)
                text = await response.text()
                _LOGGER.error("Response: %s", text)
                return False
        except Exception as ex:
            _LOGGER.error("Login failed: %s", ex)
            return False

    async def get_ticket(self) -> str | None:
        """Get a one-time ticket for media streaming."""
        if not self.token:
            if not await self.login():
                return None
        
        try:
            url = f"{self.host}/rest/v4/login/tickets"
            headers = {"Authorization": f"Bearer {self.token}"}
            
            async with self.session.post(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    ticket = result.get("token")
                    _LOGGER.debug("Ticket obtained")
                    return ticket
                elif response.status == 401:
                    # Token expired, re-login and try again
                    if await self.login():
                        return await self.get_ticket()
                _LOGGER.error("Failed to get ticket: %s", response.status)
                return None
        except Exception as ex:
            _LOGGER.error("Error getting ticket: %s", ex)
            return None

    async def get_cameras(self) -> list[dict[str, Any]]:
        """Get list of cameras from NX Witness."""
        if not self.token:
            if not await self.login():
                return []
        
        try:
            url = f"{self.host}/rest/v4/devices"
            headers = {"Authorization": f"Bearer {self.token}"}
            
            async with self.session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Filter to only cameras
                    cameras = []
                    if isinstance(data, list):
                        for device in data:
                            # Check if it's a camera device
                            if device.get("deviceType") == "Camera":
                                cameras.append({
                                    "id": device.get("id"),
                                    "name": device.get("name"),
                                    "model": device.get("model"),
                                    "status": device.get("status"),
                                })
                    _LOGGER.debug("Found %d cameras", len(cameras))
                    return cameras
                elif response.status == 401:
                    # Token expired, try to re-login
                    if await self.login():
                        return await self.get_cameras()
                _LOGGER.error("Failed to get cameras: %s", response.status)
                return []
        except Exception as ex:
            _LOGGER.error("Error getting cameras: %s", ex)
            return []

    def get_camera_stream_url(self, camera_id: str, ticket: str) -> str:
        """Get stream URL for a camera with ticket."""
        return f"{self.host}/rest/v4/devices/{camera_id}/media?_ticket={ticket}"

    async def get_server_info(self) -> dict[str, Any]:
        """Get server information."""
        if not self.token:
            if not await self.login():
                return {}

        try:
            url = f"{self.host}/rest/v4/servers"
            headers = {"Authorization": f"Bearer {self.token}"}

            async with self.session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list) and len(data) > 0:
                        return data[0]
                    return {}
                if response.status == 401:
                    if await self.login():
                        return await self.get_server_info()
                return {}
        except Exception as ex:
            _LOGGER.error("Error getting server info: %s", ex)
            return {}


    async def get_event_log(
        self,
        start_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get events from NX Witness event log."""
        if not self.token:
            if not await self.login():
                return []

        try:
            url = f"{self.host}/rest/v4/events/log"
            headers = {"Authorization": f"Bearer {self.token}"}
            params: dict[str, int] = {}

            if start_time_ms is not None:
                params["startTimeMs"] = start_time_ms

            async with self.session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        for key in ("items", "events", "data"):
                            value = data.get(key)
                            if isinstance(value, list):
                                return value
                    return []
                if response.status == 401:
                    if await self.login():
                        return await self.get_event_log(start_time_ms)
                _LOGGER.error("Failed to get event log: %s", response.status)
                return []
        except Exception as ex:
            _LOGGER.error("Error getting event log: %s", ex)
            return []

    async def get_object_tracks(
        self,
        start_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get object tracks (motion/person/vehicle detection)."""
        if not self.token:
            if not await self.login():
                return []

        try:
            url = f"{self.host}/rest/v4/analytics/objectTracks"
            headers = {"Authorization": f"Bearer {self.token}"}
            params: dict[str, int] = {}

            if start_time_ms is not None:
                params["startTimeMs"] = start_time_ms

            async with self.session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        return data
                    return []
                if response.status == 401:
                    if await self.login():
                        return await self.get_object_tracks(start_time_ms)
                _LOGGER.error("Failed to get object tracks: %s", response.status)
                return []
        except Exception as ex:
            _LOGGER.error("Error getting object tracks: %s", ex)
            return []
