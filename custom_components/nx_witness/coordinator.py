"""DataUpdateCoordinator for NX Witness."""
import logging
import ssl
from datetime import datetime, timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, EVENT_LOG_INTERVAL, OBJECT_TRACK_INTERVAL, UPDATE_INTERVAL
from .nx_client import NXWitnessClient

_LOGGER = logging.getLogger(__name__)


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
        self.client = None
        self.cameras = []
        self.object_tracks = []
        self.events = []
        self.last_camera_check = datetime.min
        self.last_track_check = datetime.min
        self.last_event_check = datetime.min
        self._session = None

        # Keep the coordinator ticking fast enough for event sensors.
        # Camera refresh is throttled separately via UPDATE_INTERVAL.
        coordinator_interval = min(EVENT_LOG_INTERVAL, OBJECT_TRACK_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=coordinator_interval),
        )

    async def _async_setup(self):
        """Set up the coordinator with SSL context."""

        def create_ssl_context():
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return ssl_context

        ssl_context = await self.hass.async_add_executor_job(create_ssl_context)

        connector = aiohttp.TCPConnector(ssl=ssl_context)
        self._session = aiohttp.ClientSession(connector=connector)

        self.client = NXWitnessClient(
            self.host,
            self.username,
            self.password,
            self._session,
        )

        if not await self.client.login():
            raise UpdateFailed("Failed to login to NX Witness")

    async def _async_update_data(self):
        """Update data via library."""
        try:
            now = datetime.now()

            if (now - self.last_camera_check).total_seconds() >= UPDATE_INTERVAL:
                cameras = await self.client.get_cameras()
                if cameras:
                    self.cameras = cameras
                self.last_camera_check = now

            if (now - self.last_track_check).total_seconds() >= OBJECT_TRACK_INTERVAL:
                start_time_ms = int((now - timedelta(minutes=1)).timestamp() * 1000)
                self.object_tracks = await self.client.get_object_tracks(start_time_ms)
                self.last_track_check = now

            if (now - self.last_event_check).total_seconds() >= EVENT_LOG_INTERVAL:
                start_time_ms = int((now - timedelta(minutes=1)).timestamp() * 1000)
                self.events = await self.client.get_event_log(start_time_ms)
                self.last_event_check = now

            return {
                "cameras": self.cameras,
                "object_tracks": self.object_tracks,
                "events": self.events,
            }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with NX Witness: {err}") from err
