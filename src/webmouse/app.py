"""The aiohttp application: routes, security middleware, WebSocket and stream."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import mimetypes
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from . import __version__
from .capture import StreamError, StreamHub, StreamKey
from .config import Config, ConfigError
from .controller import Controller
from .input import BackendError, InputBackend, create_backend
from .net import host_allowed, lan_addresses, local_host_names
from .screens import Screens
from .security import AuthManager, LoginError, NetworkPolicy, Session, resolve_pin
from .worker import InputWorker

log = logging.getLogger("webmouse")

mimetypes.add_type("application/javascript", ".js")  # Windows registries get this wrong
mimetypes.add_type("image/svg+xml", ".svg")

STATIC = Path(__file__).parent / "static"
COOKIE = "webmouse_session"
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)


@dataclass
class State:
    cfg: Config
    pin: str
    tls: bool
    auth: AuthManager
    policy: NetworkPolicy
    host_names: set
    backend: InputBackend
    worker: InputWorker
    screens: Screens
    hub: StreamHub
    controller: Controller


STATE = web.AppKey("state", State)


def _json(data: dict, status: int = 200, **kw) -> web.Response:
    return web.json_response(data, status=status, **kw)


def _origin_ok(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True  # same-origin GETs and non-browser clients send none
    try:
        return urlsplit(origin).netloc.lower() == request.host.lower()
    except ValueError:
        return False


# --------------------------------------------------------------------------
# middleware
# --------------------------------------------------------------------------
@web.middleware
async def guard(request: web.Request, handler):
    st = request.app[STATE]
    if not st.policy.allows(request.remote):
        log.warning("rejected non-LAN client %s", request.remote)
        raise web.HTTPForbidden(text="webmouse only accepts connections from the local network.")
    if not host_allowed(request.headers.get("Host", ""), st.host_names):
        raise web.HTTPForbidden(text="Unrecognised Host header.")
    if (request.method not in ("GET", "HEAD", "OPTIONS") or request.path == "/ws") \
            and not _origin_ok(request):
        raise web.HTTPForbidden(text="Cross-origin request blocked.")
    return await handler(request)


async def _on_prepare(request: web.Request, response: web.StreamResponse) -> None:
    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/static/") or request.path == "/":
        h.setdefault("Cache-Control", "no-cache")
    else:
        h.setdefault("Cache-Control", "no-store")


def _session(request: web.Request) -> Session:
    session = request.app[STATE].auth.get(request.cookies.get(COOKIE))
    if session is None:
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Not signed in."}), content_type="application/json"
        )
    return session


# --------------------------------------------------------------------------
# HTTP handlers
# --------------------------------------------------------------------------
async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "index.html")


async def api_session(request: web.Request) -> web.Response:
    st = request.app[STATE]
    s = st.auth.get(request.cookies.get(COOKIE))
    if s is None:
        return _json({"authed": False})
    return _json({"authed": True, "name": s.name, "id": s.id})


async def api_login(request: web.Request) -> web.Response:
    st = request.app[STATE]
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return _json({"error": "Invalid request."}, 400)
    try:
        session = st.auth.login(request.remote or "?", str(data.get("pin", "")), data.get("name", ""))
    except LoginError as exc:
        await asyncio.sleep(0.4)  # blunt automated guessing
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
        log.warning("failed login from %s: %s", request.remote, exc.message)
        return _json({"error": exc.message, "retry_after": exc.retry_after}, exc.status, headers=headers)

    previous = st.auth.logout(request.cookies.get(COOKIE))
    if previous is not None:
        await _close_sockets(previous, 4401)
    log.info("paired %r from %s", session.name, session.ip)
    resp = _json({"ok": True, "name": session.name, "id": session.id})
    resp.set_cookie(
        COOKIE, session.token,
        max_age=int(st.cfg.security.session_hours * 3600),
        httponly=True, samesite="Strict", secure=st.tls, path="/",
    )
    return resp


async def api_logout(request: web.Request) -> web.Response:
    st = request.app[STATE]
    s = st.auth.logout(request.cookies.get(COOKIE))
    if s is not None:
        await _close_sockets(s, 4401)
    resp = _json({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


async def api_devices(request: web.Request) -> web.Response:
    st = request.app[STATE]
    me = _session(request)
    now = time.time()
    return _json({"devices": [
        {
            "id": s.id, "name": s.name, "ip": s.ip, "current": s.id == me.id,
            "online": bool(s.sockets), "idle": int(now - s.last_seen),
        }
        for s in st.auth.sessions()
    ]})


async def api_revoke(request: web.Request) -> web.Response:
    st = request.app[STATE]
    _session(request)
    gone = st.auth.revoke(request.match_info["id"])
    if gone is None:
        return _json({"error": "Unknown device."}, 404)
    log.info("revoked %r (%s)", gone.name, gone.ip)
    await _close_sockets(gone, 4401)
    return _json({"ok": True})


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
async def _close_sockets(session: Session, code: int) -> None:
    for ws in list(session.sockets):
        try:
            await ws.close(code=code)
        except Exception:  # noqa: BLE001
            pass


def _hello(st: State, s: Session) -> dict:
    mon = st.screens.get(s.monitor) or st.screens.get(st.screens.default_index())
    cfg = st.cfg
    return {
        "t": "hello",
        "version": __version__,
        "id": s.id,
        "name": s.name,
        "platform": sys.platform,
        "backend": st.backend.name,
        "monitors": [{"i": m.index, "w": m.width, "h": m.height} for m in st.screens.physical()],
        "monitor": s.monitor,
        "screen": {"w": mon.width, "h": mon.height} if mon else None,
        "stream": {
            "enabled": cfg.stream.enabled,
            "available": st.hub.available,
            "reason": st.hub.reason,
            "fps": cfg.stream.fps,
            "quality": cfg.stream.quality,
            "scale": cfg.stream.scale,
        },
        "ui": dataclasses.asdict(cfg.ui),
    }


async def ws_handler(request: web.Request) -> web.StreamResponse:
    st = request.app[STATE]
    s = _session(request)
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=32 * 1024)
    await ws.prepare(request)
    if not s.monitor or st.screens.get(s.monitor) is None:
        s.monitor = st.screens.default_index()
    s.sockets.add(ws)
    log.info("%r (%s) connected", s.name, s.ip)
    try:
        await ws.send_json(_hello(st, s))
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            reply = st.controller.handle(s, data)
            if reply:
                await ws.send_json(reply)
    finally:
        s.sockets.discard(ws)
        if not s.sockets:
            st.controller.release_all(s)
        log.info("%r (%s) disconnected", s.name, s.ip)
    return ws


# --------------------------------------------------------------------------
# screen stream
# --------------------------------------------------------------------------
def _qnum(request: web.Request, name: str, default: float, lo: float, hi: float, cast=int):
    try:
        value = cast(request.query.get(name, default))
    except ValueError:
        value = default
    return max(lo, min(hi, value))


async def stream(request: web.Request) -> web.StreamResponse:
    st = request.app[STATE]
    _session(request)
    cfg = st.cfg.stream
    if not cfg.enabled:
        raise web.HTTPNotFound(text="Streaming is disabled.")
    if not st.hub.available:
        raise web.HTTPServiceUnavailable(text=f"Streaming unavailable: {st.hub.reason}")

    index = _qnum(request, "m", st.screens.default_index(), 0, 64)
    if st.screens.get(index) is None:
        index = st.screens.default_index()
    key = StreamKey(
        monitor=index,
        quality=_qnum(request, "q", cfg.quality, 10, cfg.quality),
        scale=round(_qnum(request, "s", cfg.scale, 0.1, cfg.scale, float) * 20) / 20 or 0.1,
        fps=_qnum(request, "fps", cfg.fps, 1, cfg.fps),
    )

    frames: AsyncIterator[bytes] = st.hub.frames(key)
    try:
        first = await frames.__anext__()
    except StreamError as exc:
        await frames.aclose()
        raise web.HTTPServiceUnavailable(text=str(exc)) from None

    resp = web.StreamResponse(headers={
        "Content-Type": "multipart/x-mixed-replace; boundary=frame",
        "Cache-Control": "no-store",
    })
    await resp.prepare(request)
    try:
        frame: Optional[bytes] = first
        while frame is not None:
            await resp.write(
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            )
            frame = await frames.__anext__()
    except (ConnectionResetError, StopAsyncIteration, StreamError):
        pass
    finally:
        await frames.aclose()
    return resp


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
async def _maintenance(st: State) -> None:
    while True:
        await asyncio.sleep(10)
        if any(s.sockets for s in st.auth.sessions()):
            await st.screens.refresh(force=True)
        for s in st.auth.reap():
            await _close_sockets(s, 4401)


async def _lifecycle(app: web.Application):
    st = app[STATE]
    await st.screens.refresh(force=True)
    await st.hub.probe()
    task = asyncio.create_task(_maintenance(st))
    yield
    task.cancel()
    st.hub.close()
    st.worker.close()
    st.backend.close()


async def _on_shutdown(app: web.Application) -> None:
    for s in app[STATE].auth.sessions():
        await _close_sockets(s, 1001)


def create_app(cfg: Config, backend: InputBackend, pin: Optional[str] = None) -> web.Application:
    pin = pin or resolve_pin(cfg.security)
    screens = Screens()
    worker = InputWorker()
    state = State(
        cfg=cfg,
        pin=pin,
        tls=cfg.tls.enabled,
        auth=AuthManager(cfg.security, pin),
        policy=NetworkPolicy(cfg.security.allowed_networks),
        host_names=local_host_names(cfg.server.allowed_hosts),
        backend=backend,
        worker=worker,
        screens=screens,
        hub=StreamHub(
            cfg.stream, screens,
            lambda: worker.call(backend.position, timeout=0.25),
        ),
        controller=Controller(cfg, backend, worker, screens),
    )
    app = web.Application(middlewares=[guard], client_max_size=16 * 1024)
    app[STATE] = state
    app.on_response_prepare.append(_on_prepare)
    app.on_shutdown.append(_on_shutdown)
    app.cleanup_ctx.append(_lifecycle)

    app.router.add_get("/", index)
    app.router.add_get("/api/session", api_session)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/devices", api_devices)
    app.router.add_post("/api/devices/{id}/revoke", api_revoke)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/stream.mjpg", stream)
    app.router.add_static("/static", STATIC, follow_symlinks=False)
    return app


# --------------------------------------------------------------------------
# startup banner and run loop
# --------------------------------------------------------------------------
def _urls(cfg: Config) -> list[str]:
    if cfg.server.public_url:
        return [cfg.server.public_url.rstrip("/")]
    scheme = "https" if cfg.tls.enabled else "http"
    host = cfg.server.host
    hosts = lan_addresses() or ["127.0.0.1"] if host in ("0.0.0.0", "::", "") else [host]
    return [f"{scheme}://{h}:{cfg.server.port}" for h in hosts]


def _print_banner(app: web.Application, cfg: Config, show_qr: bool, config_path) -> None:
    st = app[STATE]
    urls = _urls(cfg)
    out = ["", f"  webmouse {__version__}", "", "  Open this on your phone or tablet (same Wi-Fi/LAN):"]
    out += [f"    {u}" for u in urls]
    fixed = cfg.security.pin.lower() != "auto"
    out += ["", f"  PIN: {st.pin}" + ("   (from config)" if fixed else ""), ""]
    print("\n".join(out))
    if show_qr:
        try:
            import qrcode

            link = urls[0] + (f"#pin={st.pin}" if cfg.security.qr_includes_pin else "")
            qr = qrcode.QRCode(border=1)
            qr.add_data(link)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
            print("  Scan the QR code" + (" to sign in automatically." if cfg.security.qr_includes_pin else "."))
        except Exception as exc:  # noqa: BLE001 - the QR code is a convenience only
            log.debug("QR code skipped: %s", exc)
    stream = "on" if st.hub.available else f"off ({st.hub.reason})"
    print(
        f"\n  Input: {st.backend.name}   Screen stream: {stream}   "
        f"TLS: {'on' if cfg.tls.enabled else 'off'}\n"
        f"  Config: {config_path or 'built-in defaults'}\n"
        "  Press Ctrl+C to stop.\n",
        flush=True,
    )


async def _serve(app: web.Application, cfg: Config, ssl_ctx, show_qr: bool, config_path) -> None:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(
            runner, cfg.server.host, cfg.server.port, ssl_context=ssl_ctx, shutdown_timeout=2.0
        )
        await site.start()
        _print_banner(app, cfg, show_qr, config_path)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: KeyboardInterrupt is used instead
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 0.5)  # short ticks keep Ctrl+C responsive on Windows
            except asyncio.TimeoutError:
                pass
    finally:
        await runner.cleanup()


def run(cfg: Config, show_qr: bool = True, config_path: Optional[Path] = None) -> int:
    from .platform_setup import prepare_process

    prepare_process()
    try:
        backend = create_backend(cfg.input.backend)
    except BackendError as exc:
        print(f"webmouse: {exc}", file=sys.stderr)
        return 1

    ssl_ctx = None
    if cfg.tls.enabled:
        from .tls import make_context

        try:
            ssl_ctx = make_context(cfg)
        except ConfigError as exc:
            print(f"webmouse: {exc}", file=sys.stderr)
            return 2

    policy = NetworkPolicy(cfg.security.allowed_networks)
    if cfg.server.host not in ("0.0.0.0", "::", "") and not policy.allows(cfg.server.host):
        log.warning("listening on %s, which is not a LAN address", cfg.server.host)

    app = create_app(cfg, backend)
    try:
        asyncio.run(_serve(app, cfg, ssl_ctx, show_qr, config_path))
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"webmouse: cannot listen on {cfg.server.host}:{cfg.server.port}: {exc}", file=sys.stderr)
        return 1
    return 0
