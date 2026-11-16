"""HTTP view that proxies camera streams from NX Witness.

NX Witness `_ticket` authorization is single-use, and ffmpeg/go2rtc open the
source URL multiple times (probe, read, reconnects), so we can't embed a
ticket in `stream_source`. Instead `stream_source` points at this view, which
mints a fresh ticket per request and relays the media itself.

We relay rather than redirect because NX servers typically use self-signed
certificates (often without IP SANs). Our aiohttp session tolerates that;
go2rtc does not, so a redirect to the NX HTTPS URL fails TLS verification.
Relaying also lets the coordinator pick whichever server in a merged system
currently owns the camera.
"""
from __future__ import annotations

import asyncio
import logging
import secrets

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STREAM_PATH = "/api/nx_witness/stream/{entry_id}/{secret}/{camera_id}"
CHUNK_SIZE = 64 * 1024
_PASSTHROUGH_HEADERS = ("Content-Type",)


class NXWitnessStreamView(HomeAssistantView):
    """Mints a fresh NX ticket and relays the camera media stream."""

    url = STREAM_PATH
    name = "api:nx_witness:stream"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        secret: str,
        camera_id: str,
    ) -> web.StreamResponse:
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None or not secrets.compare_digest(
            secret, getattr(coordinator, "stream_secret", "")
        ):
            return web.Response(status=404)

        minted = await coordinator.async_camera_ticket(camera_id)
        if minted is None:
            _LOGGER.error("Failed to mint ticket for camera %s", camera_id)
            return web.Response(status=502)
        client, ticket = minted

        upstream_url = client.get_camera_stream_url(camera_id, ticket)
        try:
            upstream = await client.session.get(
                upstream_url,
                params=dict(request.query),
                timeout=aiohttp.ClientTimeout(
                    total=None, connect=10, sock_connect=10, sock_read=30
                ),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as ex:
            _LOGGER.error(
                "Cannot open stream for camera %s via %s: %s",
                camera_id,
                client.host,
                ex,
            )
            return web.Response(status=502)

        response: web.StreamResponse | None = None
        try:
            if upstream.status != 200:
                _LOGGER.error(
                    "NX server %s returned %s for camera %s stream",
                    client.host,
                    upstream.status,
                    camera_id,
                )
                return web.Response(status=upstream.status)

            response = web.StreamResponse(status=200)
            for header in _PASSTHROUGH_HEADERS:
                if header in upstream.headers:
                    response.headers[header] = upstream.headers[header]
            await response.prepare(request)

            async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                await response.write(chunk)
            return response
        except (ConnectionResetError, asyncio.CancelledError):
            # Consumer (ffmpeg/go2rtc) hung up; normal on stop/reconnect.
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as ex:
            _LOGGER.warning("Stream for camera %s interrupted: %s", camera_id, ex)
            return response if response is not None else web.Response(status=502)
        finally:
            # close(), not release(): the live stream never ends, so the
            # connection can't be returned to the pool.
            upstream.close()


def stream_path_for(entry_id: str, secret: str, camera_id: str) -> str:
    return f"/api/nx_witness/stream/{entry_id}/{secret}/{camera_id}"
