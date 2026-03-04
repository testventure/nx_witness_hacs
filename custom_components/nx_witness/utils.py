"""Shared utilities for the NX Witness integration."""
import ssl
from typing import Any

import aiohttp


def create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that accepts self-signed certificates."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def create_client_session(ssl_context: ssl.SSLContext) -> aiohttp.ClientSession:
    """Create an aiohttp ClientSession using the given SSL context."""
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context))


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Return the nested eventData payload when present, otherwise the event itself."""
    nested = event.get("eventData")
    return nested if isinstance(nested, dict) else event


def extract_camera_id(event: dict[str, Any]) -> str | None:
    """Extract the camera/device id from an event, checking common field names."""
    payload = event_payload(event)
    for source in (payload, event):
        for field in ("cameraId", "deviceId", "resourceId", "sourceId"):
            value = source.get(field)
            if isinstance(value, str) and value:
                return value
    return None
