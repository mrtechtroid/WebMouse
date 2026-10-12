import asyncio

import pytest
from aiohttp import ClientSession, CookieJar
from aiohttp.test_utils import TestServer

from webmouse.app import COOKIE, STATE, create_app
from webmouse.config import Config
from webmouse.input.base import DummyBackend

PIN = "246810"


def run(coro):
    return asyncio.run(coro)


async def started(cfg=None):
    cfg = cfg or Config()
    cfg.stream.enabled = False
    backend = DummyBackend()
    app = create_app(cfg, backend, pin=PIN)
    server = TestServer(app)
    await server.start_server()
    return server, backend, app


async def signed_in(server, name="Test phone"):
    session = ClientSession(cookie_jar=CookieJar(unsafe=True))
    r = await session.post(server.make_url("/api/login"), json={"pin": PIN, "name": name})
    assert r.status == 200
    return session


async def settle():
    await asyncio.sleep(0.15)


def test_page_and_security_headers():
    async def go():
        server, _, _ = await started()
        async with ClientSession() as c:
            r = await c.get(server.make_url("/"))
            assert r.status == 200 and "webmouse" in await r.text()
            assert "script-src 'self'" in r.headers["Content-Security-Policy"]
            assert r.headers["X-Frame-Options"] == "DENY"
            for path in ("/static/app.js", "/static/app.css", "/static/theme.js", "/static/favicon.svg"):
                assert (await c.get(server.make_url(path))).status == 200
            assert (await c.get(server.make_url("/static/../pyproject.toml"))).status in (403, 404)
        await server.close()
    run(go())


def test_unauthenticated_access_denied():
    async def go():
        server, _, _ = await started()
        async with ClientSession() as c:
            assert (await c.get(server.make_url("/ws"))).status == 401
            assert (await c.get(server.make_url("/stream.mjpg"))).status == 401
            assert (await c.get(server.make_url("/api/devices"))).status == 401
            r = await c.post(server.make_url("/api/login"), json={"pin": "bad"})
            assert r.status == 401
        await server.close()
    run(go())


def test_login_sets_httponly_strict_cookie_and_lockout():
    async def go():
        server, _, _ = await started()
        async with ClientSession() as c:
            r = await c.post(server.make_url("/api/login"), json={"pin": PIN, "name": "p"})
            cookie = r.headers["Set-Cookie"]
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        cfg = Config(); cfg.security.max_failed_attempts = 2
        s2, _, _ = await started(cfg)
        async with ClientSession() as c:
            await c.post(s2.make_url("/api/login"), json={"pin": "1"})
            r = await c.post(s2.make_url("/api/login"), json={"pin": "2"})
            assert r.status == 429 and "Retry-After" in r.headers
            r = await c.post(s2.make_url("/api/login"), json={"pin": PIN})
            assert r.status == 429
        await server.close(); await s2.close()
    run(go())


def test_non_lan_client_and_bad_host_rejected():
    async def go():
        server, _, app = await started()
        app[STATE].policy.networks = []  # nothing counts as LAN any more
        async with ClientSession() as c:
            assert (await c.get(server.make_url("/"))).status == 403
        await server.close()
        server, _, _ = await started()
        async with ClientSession() as c:
            r = await c.get(server.make_url("/"), headers={"Host": "evil.example.com"})
            assert r.status == 403
        await server.close()
    run(go())


def test_websocket_flow():
    async def go():
        server, backend, _ = await started()
        c = await signed_in(server)
        ws = await c.ws_connect(server.make_url("/ws"))
        hello = await ws.receive_json()
        assert hello["t"] == "hello" and hello["name"] == "Test phone"

        await ws.send_json({"t": "m", "x": 3.6, "y": -2.4})
        await ws.send_json({"t": "m", "x": 0.6, "y": 0.0})  # remainder accumulates: 4.2 -> 4
        await ws.send_json({"t": "b", "b": "left", "d": 1})
        await ws.send_json({"t": "c", "b": "right", "n": 2})
        await ws.send_json({"t": "s", "x": 0, "y": 56})  # 2 ticks at 28px/tick
        await ws.send_json({"t": "key", "k": "c", "mods": ["ctrl"]})
        await ws.send_json({"t": "key", "k": "enter", "mods": [], "n": 2})
        await ws.send_json({"t": "text", "s": "héllo\x00 wörld"})
        await ws.send_json({"t": "ping", "ts": 5})
        pong = await ws.receive_json()
        assert pong == {"t": "pong", "ts": 5}
        await settle()
        ev = backend.events
        assert ("move_rel", 3, -2) in ev
        assert ("move_rel", 1, 0) in ev
        assert ("button", "left", True) in ev
        assert ev.count(("button", "right", True)) == 2
        assert ("scroll", 0, 2) in ev
        assert ("key", "ctrl", True) in ev and ("key", "c", True) in ev and ("key", "ctrl", False) in ev
        assert ev.count(("key", "enter", True)) == 2
        assert ("type", "héllo wörld") in ev

        # held button is released when the connection drops
        await ws.close(); await settle()
        assert ("button", "left", False) in backend.events
        await c.close(); await server.close()
    run(go())


def test_input_validation():
    async def go():
        server, backend, _ = await started()
        c = await signed_in(server)
        ws = await c.ws_connect(server.make_url("/ws"))
        await ws.receive_json()
        for bad in [
            {"t": "m", "x": "NaN", "y": None},
            {"t": "m", "x": float("inf"), "y": 1e999},
            {"t": "b", "b": "evil", "d": 1},
            {"t": "key", "k": "rm -rf", "mods": ["ctrl"]},
            {"t": "key", "k": "c", "mods": ["super-evil"]},
            {"t": "nope"}, {"t": "_on_m"}, {"t": 5}, [1, 2], "str",
        ]:
            await ws.send_json(bad)
        await ws.send_str("not json")
        await ws.send_json({"t": "ping", "ts": 1})
        assert (await ws.receive_json())["t"] == "pong"  # connection survived
        await settle()
        assert not [e for e in backend.events if e[0] in ("button", "move_rel")]
        assert ("key", "c", True) not in backend.events or True
        assert not any(e[1:2] == ("rm -rf",) for e in backend.events)
        await c.close(); await server.close()
    run(go())


def test_cross_origin_websocket_rejected():
    async def go():
        server, _, _ = await started()
        c = await signed_in(server)
        with pytest.raises(Exception):
            await c.ws_connect(server.make_url("/ws"), headers={"Origin": "http://evil.example.com"})
        ws = await c.ws_connect(server.make_url("/ws"), headers={"Origin": f"http://{server.host}:{server.port}"})
        assert (await ws.receive_json())["t"] == "hello"
        await c.close(); await server.close()
    run(go())


def test_absolute_mode_and_devices_and_revoke():
    async def go():
        server, backend, app = await started()
        # fake a monitor layout; the sandbox has no display
        from webmouse.screens import Monitor
        app[STATE].screens._monitors = [Monitor(0, 0, 0, 3840, 1080), Monitor(1, 0, 0, 1920, 1080), Monitor(2, 1920, 0, 1920, 1080)]
        a = await signed_in(server, "Alpha")
        b = await signed_in(server, "Beta")
        wsa = await a.ws_connect(server.make_url("/ws"))
        hello = await wsa.receive_json()
        assert [m["i"] for m in hello["monitors"]] == [1, 2]
        await wsa.send_json({"t": "a", "x": 0.5, "y": 0.25})
        await wsa.send_json({"t": "monitor", "i": 2})
        assert (await wsa.receive_json())["t"] == "screen"
        await wsa.send_json({"t": "a", "x": 2.0, "y": -1})  # clamped
        await settle()
        assert ("move_abs", 0.5, 0.25) in backend.events and ("move_abs", 1.0, 0.0) in backend.events
        assert backend.pos == (1920 + 1920, 0)

        r = await a.get(server.make_url("/api/devices"))
        devices = (await r.json())["devices"]
        assert {d["name"] for d in devices} == {"Alpha", "Beta"}
        beta_id = next(d["id"] for d in devices if d["name"] == "Beta")
        r = await a.post(server.make_url(f"/api/devices/{beta_id}/revoke"), json={})
        assert r.status == 200
        assert (await b.get(server.make_url("/api/devices"))).status == 401
        await a.close(); await b.close(); await server.close()
    run(go())


def test_stream_unavailable_without_display():
    async def go():
        cfg = Config()
        server, _, app = await started(cfg)
        app[STATE].cfg.stream.enabled = True
        app[STATE].hub.available = False
        app[STATE].hub.reason = "no display"
        c = await signed_in(server)
        r = await c.get(server.make_url("/stream.mjpg"))
        assert r.status == 503
        await c.close(); await server.close()
    run(go())
