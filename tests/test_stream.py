"""Failure-mode tests for the supervised event stream, against a fake Particle client.

Run: <venv with homeassistant>/bin/python -m pytest tests/  (or run this file directly)
"""
import asyncio
import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.particle import const  # noqa: E402

const.PROBE_INTERVAL, const.PROBE_TIMEOUT = 0.3, 0.3
const.BACKOFF_MIN, const.BACKOFF_MAX = 0.05, 0.2
stream_mod = importlib.import_module("custom_components.particle.stream")
for k in ("PROBE_INTERVAL", "PROBE_TIMEOUT", "BACKOFF_MIN", "BACKOFF_MAX"):
    setattr(stream_mod, k, getattr(const, k))
stream_mod.DOWN_ISSUE_AFTER = 0.5
stream_mod.PROBE_SETTLE = 0.01

issues: dict = {}
stream_mod.ir = types.SimpleNamespace(
    async_create_issue=lambda hass, dom, iid, **kw: issues.__setitem__(iid, kw),
    async_delete_issue=lambda hass, dom, iid: issues.pop(iid, None),
    IssueSeverity=types.SimpleNamespace(WARNING="warning"),
)
stream_mod.async_dispatcher_send = lambda *a: None


def fake_call_later(hass, delay, cb):
    h = asyncio.get_running_loop().call_later(delay, cb, None)
    return h.cancel


stream_mod.async_call_later = fake_call_later

from custom_components.particle.api import ParticleAuthError  # noqa: E402


class FakeClient:
    """mode per connection: 'ok' echoes canaries, 'zombie' keeps alive but drops them,
    'die' raises a read timeout after a bit, 'down' can't connect, 'auth' -> 401."""

    def __init__(self, modes):
        self.modes = list(modes)
        self.connections = 0
        self.q: asyncio.Queue | None = None

    async def publish(self, name, data="", private=True, ttl=60):
        if self.q:
            await self.q.put((name, {"data": data, "coreid": "api"}))

    async def events(self):
        mode = self.modes[min(self.connections, len(self.modes) - 1)]
        self.connections += 1
        if mode == "auth":
            raise ParticleAuthError("401")
        if mode == "down":
            raise OSError("network unreachable")
        self.q = q = asyncio.Queue()
        yield "", {}
        await q.put(("spark/status", {"data": "online", "coreid": "dev1"}))
        t0 = asyncio.get_running_loop().time()
        while True:
            if mode == "die" and asyncio.get_running_loop().time() - t0 > 0.2:
                raise TimeoutError("sock_read")
            try:
                name, p = await asyncio.wait_for(q.get(), 0.05)
            except TimeoutError:
                yield "", {}  # keepalive
                continue
            if mode == "zombie" and name == const.PROBE_EVENT:
                continue  # the failure: canary never comes back
            yield name, p


def make(modes):
    client = FakeClient(modes)
    seen, reconnects, auth = [], [], []
    s = stream_mod.EventStream(None, "e1", client, lambda n, p: seen.append(n),
                               lambda: reconnects.append(1), lambda: auth.append(1))
    return s, client, seen, reconnects, auth


async def run_for(s, secs):
    t = asyncio.create_task(s.run())
    await asyncio.sleep(secs)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)


def test_healthy_stream_stays_on_one_connection():
    async def go():
        s, c, seen, rec, _ = make(["ok"])
        await run_for(s, 1.2)
        assert c.connections == 1 and s.healthy and not rec
        assert "spark/status" in seen and s.last_probe_ms is not None
    asyncio.run(go())


def test_zombie_stream_is_detected_and_replaced():
    async def go():
        issues.clear()
        s, c, seen, rec, _ = make(["zombie", "ok"])
        await run_for(s, 1.5)
        assert c.connections == 2, c.connections
        assert s.healthy and rec == [1] and "canary" in s.last_error
        assert not issues
    asyncio.run(go())


def test_dead_socket_reconnects_with_backoff():
    async def go():
        s, c, _, rec, _ = make(["die", "die", "ok"])
        await run_for(s, 1.5)
        assert c.connections == 3 and s.healthy and len(rec) == 2
    asyncio.run(go())


def test_long_outage_raises_then_clears_repair_issue():
    async def go():
        issues.clear()
        s, c, *_ = make(["down"] * 7 + ["ok"])  # ~1.2 s outage
        t = asyncio.create_task(s.run())
        await asyncio.sleep(0.75)
        assert "stream_down_e1" in issues
        await asyncio.sleep(1.2)
        assert s.healthy and not issues
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    asyncio.run(go())


def test_auth_failure_stops_and_asks_for_reauth():
    async def go():
        s, c, _, _, auth = make(["auth"])
        await asyncio.wait_for(s.run(), 2)
        assert auth == [1] and not s.healthy
    asyncio.run(go())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
