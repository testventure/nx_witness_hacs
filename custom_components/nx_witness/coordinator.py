"""DataUpdateCoordinator for NX Witness."""
import ipaddress
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, EVENT_LOG_INTERVAL, SERVER_RETRY_INTERVAL, UPDATE_INTERVAL
from .nx_client import NXWitnessClient
from .utils import create_client_session, create_ssl_context, extract_camera_id

_LOGGER = logging.getLogger(__name__)


def _origin(url: str) -> str | None:
    """Return scheme://host:port for a URL, or None if it isn't usable."""
    try:
        parts = urlsplit(url if "//" in url else f"https://{url}")
    except ValueError:
        return None
    if not parts.hostname:
        return None
    try:
        if ipaddress.ip_address(parts.hostname).is_loopback:
            return None
    except ValueError:
        if parts.hostname == "localhost":
            return None
    scheme = parts.scheme or "https"
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = parts.port
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


class NXWitnessDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching NX Witness data."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.host = host
        self.username = username
        self.password = password
        self.client: NXWitnessClient | None = None
        self.cameras: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.servers: dict[str, dict[str, Any]] = {}
        self.last_camera_check = datetime.min
        self._session = None
        self.stream_secret = secrets.token_urlsafe(16)

        # The client the user configured; never replaced, so we can return to it.
        self._primary_client: NXWitnessClient | None = None
        # One logged-in client per other server in the system, keyed by server id.
        self._server_clients: dict[str, NXWitnessClient] = {}
        # server id -> monotonic time of the last failed connection attempt.
        self._server_failures: dict[str, float] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=EVENT_LOG_INTERVAL),
        )

    async def _async_setup(self):
        """Set up the coordinator with SSL context."""
        ssl_context = await self.hass.async_add_executor_job(create_ssl_context)
        self._session = create_client_session(ssl_context)
        self._primary_client = self._new_client(self.host)
        self.client = self._primary_client
        if not await self.client.login():
            raise UpdateFailed("Failed to login to NX Witness")

    def _new_client(self, host: str) -> NXWitnessClient:
        return NXWitnessClient(host, self.username, self.password, self._session)

    # ------------------------------------------------------------------
    # Multi-server handling
    # ------------------------------------------------------------------

    def _server_origins(self, server: dict[str, Any]) -> list[str]:
        """Candidate base URLs for a server, most preferred first."""
        candidates: list[str] = []
        for raw in [server.get("url"), *server.get("endpoints", [])]:
            if not isinstance(raw, str) or not raw:
                continue
            origin = _origin(raw)
            if origin and origin not in candidates:
                candidates.append(origin)
        return candidates

    def _is_primary_server(self, server_id: str) -> bool:
        server = self.servers.get(server_id)
        if not server:
            return False
        primary = _origin(self.host)
        return primary is not None and primary in self._server_origins(server)

    async def _client_for_server(self, server_id: str) -> NXWitnessClient | None:
        """Return a logged-in client talking directly to the given server."""
        client = self._server_clients.get(server_id)
        if client and client.token:
            return client

        failed_at = self._server_failures.get(server_id)
        if failed_at and time.monotonic() - failed_at < SERVER_RETRY_INTERVAL:
            return None

        server = self.servers.get(server_id)
        if not server or str(server.get("status", "")).lower() == "offline":
            return None

        for origin in self._server_origins(server):
            candidate = self._new_client(origin)
            if await candidate.login():
                _LOGGER.debug(
                    "Connected directly to server %s at %s",
                    server.get("name") or server_id,
                    origin,
                )
                self._server_clients[server_id] = candidate
                self._server_failures.pop(server_id, None)
                return candidate

        _LOGGER.warning(
            "Could not reach NX server %s directly; will route through %s",
            server.get("name") or server_id,
            self.client.host if self.client else self.host,
        )
        self._server_failures[server_id] = time.monotonic()
        return None

    def _drop_server_client(self, server_id: str) -> None:
        self._server_clients.pop(server_id, None)
        self._server_failures[server_id] = time.monotonic()

    def _camera_server_id(self, camera_id: str) -> str | None:
        for camera in self.cameras:
            if camera.get("id") == camera_id:
                return camera.get("server_id")
        return None

    async def async_camera_ticket(
        self, camera_id: str
    ) -> tuple[NXWitnessClient, str] | None:
        """Mint a ticket on the server best placed to serve this camera.

        Cameras moved to another server in a merged system are served directly
        by their owning server; if it isn't reachable we fall back to the
        server we're connected to, which proxies within the system.
        """
        server_id = self._camera_server_id(camera_id)
        if server_id and not self._is_primary_server(server_id):
            client = await self._client_for_server(server_id)
            if client:
                ticket = await client.get_ticket()
                if ticket:
                    return client, ticket
                self._drop_server_client(server_id)

        if self.client is None:
            return None
        ticket = await self.client.get_ticket()
        if ticket:
            return self.client, ticket
        return None

    async def _async_failover(self) -> bool:
        """Switch the API client to another reachable server in the system."""
        candidates: list[NXWitnessClient] = []
        if self._primary_client and self.client is not self._primary_client:
            candidates.append(self._primary_client)
        for server_id in self.servers:
            if self._is_primary_server(server_id):
                continue
            client = await self._client_for_server(server_id)
            if client and client is not self.client:
                candidates.append(client)

        for candidate in candidates:
            if candidate.token or await candidate.login():
                _LOGGER.warning(
                    "NX server %s unreachable; switching to %s",
                    self.client.host if self.client else self.host,
                    candidate.host,
                )
                self.client = candidate
                return True
        return False

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _async_refresh_topology(self) -> bool:
        """Refresh servers and cameras. Returns False if the API was unreachable."""
        servers = await self.client.get_servers()
        if servers is not None:
            self.servers = {s["id"]: s for s in servers}
            # Forget clients for servers that left the system.
            for server_id in list(self._server_clients):
                if server_id not in self.servers:
                    self._server_clients.pop(server_id)

        cameras = await self.client.get_cameras()
        if cameras is None:
            return self.client.reachable
        previous = {c["id"]: c.get("server_id") for c in self.cameras}
        for camera in cameras:
            old = previous.get(camera["id"])
            if old and old != camera.get("server_id"):
                _LOGGER.info(
                    "Camera %s moved from server %s to %s",
                    camera.get("name") or camera["id"],
                    old,
                    camera.get("server_id"),
                )
        self.cameras = cameras
        return True

    async def _async_update_data(self):
        """Update data via library."""
        try:
            now = datetime.now()

            # Prefer the configured server again once it's back.
            if (
                self.client is not self._primary_client
                and self._primary_client is not None
                and await self._primary_client.login()
            ):
                _LOGGER.info("Configured NX server %s is back", self.host)
                self.client = self._primary_client
                self.last_camera_check = datetime.min

            if (now - self.last_camera_check).total_seconds() >= UPDATE_INTERVAL:
                if not await self._async_refresh_topology():
                    if not await self._async_failover():
                        raise UpdateFailed(f"Cannot reach any NX server ({self.host})")
                    await self._async_refresh_topology()
                self.last_camera_check = now

            start_time_ms = int((now - timedelta(minutes=1)).timestamp() * 1000)
            self.events = await self.client.get_event_log(start_time_ms)
            if not self.client.reachable:
                # Force a topology refresh (and failover) on the next poll.
                self.last_camera_check = datetime.min
                raise UpdateFailed(f"Lost connection to NX server {self.client.host}")

            # Pre-index events by camera_id for O(1) sensor lookup
            events_by_camera: dict[str, list] = {}
            for ev in self.events:
                cam_id = extract_camera_id(ev)
                if cam_id:
                    events_by_camera.setdefault(cam_id, []).append(ev)

            return {
                "cameras": self.cameras,
                "events": self.events,
                "events_by_camera": events_by_camera,
            }

        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error communicating with NX Witness: {err}") from err

    async def async_shutdown(self) -> None:
        """Clean up resources."""
        await super().async_shutdown()
        if self._session and not self._session.closed:
            await self._session.close()
