"""Supervised Particle event stream.

SSE fails in two ways and this module guards against both:

1. The connection dies (Wi-Fi drop, NAT timeout, cloud restart). Particle sends a
   keepalive about every 9 s, so `sock_read=60` in the client raises and we
   reconnect with jittered exponential backoff.
2. The connection stays up, with keepalives still arriving, but events stop being
   delivered (a known Particle failure). Every PROBE_INTERVAL we publish a private
   canary event with a fresh nonce; if it does not come back on our own stream
   within PROBE_TIMEOUT, the stream is torn down and reopened.

After every reconnect the coordinator refreshes, so online state and variables
missed during the gap are caught up. If the stream stays unhealthy for
DOWN_ISSUE_AFTER, a Repairs issue appears in HA; it clears itself on recovery.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import ParticleAuthError, ParticleClient, ParticleError
from .const import (BACKOFF_MAX, BACKOFF_MIN, DOMAIN, DOWN_ISSUE_AFTER, PROBE_EVENT,
                    PROBE_INTERVAL, PROBE_SETTLE, PROBE_TIMEOUT, SIGNAL_STREAM)

_LOGGER = logging.getLogger(__name__)


class StreamStuck(Exception):
    """Keepalives arrive but our canary never came back."""


class EventStream:
    def __init__(self, hass: HomeAssistant, entry_id: str, client: ParticleClient,
                 on_event: Callable[[str, dict], None],
                 on_reconnect: Callable[[], None],
                 on_auth_failed: Callable[[], None]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self._on_event = on_event
        self._on_reconnect = on_reconnect
        self._on_auth_failed = on_auth_failed
        self._nonce: str | None = None
        self._probe_back = asyncio.Event()
        self._opened = asyncio.Event()
        self._issue_timer: Callable[[], None] | None = None
        # state shown on the diagnostic entity
        self.healthy = False
        self.connects = 0
        self.last_event: str | None = None
        self.last_probe_ok: str | None = None
        self.last_probe_ms: int | None = None
        self.last_error: str | None = None
        self._unhealthy_since: float | None = time.monotonic()

    # --- public ----------------------------------------------------------------

    async def run(self) -> None:
        """Run forever (until cancelled on unload)."""
        self._arm_issue_timer()
        backoff = BACKOFF_MIN
        try:
            while True:
                opened_at = time.monotonic()
                try:
                    await self._session()
                    self.last_error = "stream closed by Particle"
                except ParticleAuthError as err:
                    self.last_error = str(err)
                    self._set_healthy(False)
                    _LOGGER.error("Particle token rejected, event stream stopped")
                    self._on_auth_failed()
                    return
                except asyncio.CancelledError:
                    raise
                except StreamStuck as err:
                    self.last_error = str(err)
                    _LOGGER.warning("Particle event stream stuck (%s), reconnecting", err)
                except Exception as err:  # noqa: BLE001
                    self.last_error = f"{type(err).__name__}: {err}"
                    _LOGGER.warning("Particle event stream lost: %s", self.last_error)
                self._set_healthy(False)
                if time.monotonic() - opened_at > 2 * PROBE_INTERVAL:
                    backoff = BACKOFF_MIN  # it was fine for a while: retry quickly
                delay = backoff * random.uniform(0.8, 1.2)
                _LOGGER.debug("reconnecting event stream in %.0f s", delay)
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, BACKOFF_MAX)
        finally:
            self._cancel_issue_timer()

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "connects": self.connects,
            "last_event": self.last_event,
            "last_probe_ok": self.last_probe_ok,
            "last_probe_ms": self.last_probe_ms,
            "last_error": self.last_error,
        }

    # --- internals ---------------------------------------------------------------

    async def _session(self) -> None:
        """One connection: consume + probe until either fails."""
        self._opened.clear()
        consume = asyncio.create_task(self._consume())
        probe = asyncio.create_task(self._probe_loop())
        try:
            done, _ = await asyncio.wait({consume, probe}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (consume, probe):
                t.cancel()
            await asyncio.gather(consume, probe, return_exceptions=True)
        for t in done:
            t.result()  # re-raise whatever ended the session

    async def _consume(self) -> None:
        async for name, payload in self.client.events():
            if not self._opened.is_set():
                self._opened.set()
                self.connects += 1
                _LOGGER.debug("Particle event stream open (#%s)", self.connects)
                if self.connects > 1:
                    self._on_reconnect()  # catch up on what the gap missed
            if not name:
                continue  # keepalive
            self.last_event = dt_util.utcnow().isoformat()
            if name == PROBE_EVENT:
                if self._nonce and payload.get("data") == self._nonce:
                    self._probe_back.set()
                continue
            try:
                self._on_event(name, payload)
            except Exception:  # noqa: BLE001 - one bad event must not kill the stream
                _LOGGER.exception("error handling Particle event %s", name)

    async def _probe_loop(self) -> None:
        await asyncio.wait_for(self._opened.wait(), timeout=60)
        await asyncio.sleep(PROBE_SETTLE)  # let the subscription settle first
        while True:
            self._nonce = uuid.uuid4().hex
            self._probe_back.clear()
            sent = time.monotonic()
            try:
                await self.client.publish(PROBE_EVENT, self._nonce, ttl=60)
            except ParticleAuthError:
                raise
            except ParticleError as err:
                # Can't publish: inconclusive, the read timeout still guards the socket.
                _LOGGER.debug("canary publish failed: %s", err)
                await asyncio.sleep(60)
                continue
            try:
                await asyncio.wait_for(self._probe_back.wait(), timeout=PROBE_TIMEOUT)
            except TimeoutError:
                raise StreamStuck(f"canary not echoed within {PROBE_TIMEOUT} s") from None
            self.last_probe_ms = round((time.monotonic() - sent) * 1000)
            self.last_probe_ok = dt_util.utcnow().isoformat()
            self._set_healthy(True)
            await asyncio.sleep(PROBE_INTERVAL)

    @callback
    def _set_healthy(self, healthy: bool) -> None:
        if healthy == self.healthy:
            if healthy:
                self._notify()  # probe stats changed
            return
        self.healthy = healthy
        if healthy:
            if self._unhealthy_since is not None:
                _LOGGER.info("Particle event stream healthy after %.0f s",
                             time.monotonic() - self._unhealthy_since)
            self._unhealthy_since = None
            self._cancel_issue_timer()
            ir.async_delete_issue(self.hass, DOMAIN, f"stream_down_{self.entry_id}")
        else:
            self._unhealthy_since = time.monotonic()
            self._arm_issue_timer()
        self._notify()

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_STREAM.format(entry_id=self.entry_id))

    @callback
    def _arm_issue_timer(self) -> None:
        self._cancel_issue_timer()

        @callback
        def _raise(_now) -> None:
            self._issue_timer = None
            if not self.healthy:
                ir.async_create_issue(
                    self.hass, DOMAIN, f"stream_down_{self.entry_id}",
                    is_fixable=False, severity=ir.IssueSeverity.WARNING,
                    translation_key="stream_down",
                    translation_placeholders={
                        "minutes": str(DOWN_ISSUE_AFTER // 60),
                        "error": self.last_error or "unknown",
                    },
                )

        self._issue_timer = async_call_later(self.hass, DOWN_ISSUE_AFTER, _raise)

    @callback
    def _cancel_issue_timer(self) -> None:
        if self._issue_timer:
            self._issue_timer()
            self._issue_timer = None
