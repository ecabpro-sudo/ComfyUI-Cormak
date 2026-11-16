"""Bind OAuth and Comfy prompt execution to the browser that queued the job."""
import base64
from contextlib import closing
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import web
import folder_paths
from comfy.cli_args import args
from comfy_execution.utils import get_executing_context

from .client import CODE, QUALITIES, CormakError, Session, Transport, authorize_url, register

COOKIE = "cormak_browser"
CALLBACK = "/cormak/oauth/callback"
QUEUE_PATHS = ("/prompt", "/api/prompt")
NODE_IDS = frozenset(("CormakTextTo3D", "CormakImageTo3D", "Cormak3DResult", "CormakAccount", "CormakGenerationStatus"))
TICKET_TTL = 12 * 3600
_browser_from_request = ContextVar("cormak_browser_from_request", default=None)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Journal:
    """Public registration IDs and idempotency records only. No content or tokens."""
    def __init__(self, path):
        self.path = Path(path)

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS clients (browser TEXT, redirect TEXT, client TEXT, PRIMARY KEY(browser,redirect))")
            con.execute("CREATE TABLE IF NOT EXISTS intents (fingerprint TEXT PRIMARY KEY, job TEXT, cap INTEGER, browser TEXT, epoch TEXT)")
        except BaseException:
            con.close()
            raise
        return con

    def client(self, browser, redirect, client=None):
        with closing(self._open()) as con, con:
            if client is not None:
                con.execute("INSERT OR IGNORE INTO clients VALUES (?, ?, ?)", (browser, redirect, client))
            row = con.execute("SELECT client FROM clients WHERE browser=? AND redirect=?", (browser, redirect)).fetchone()
        return row[0] if row else None

    def intent(self, fingerprint, new=None):
        with closing(self._open()) as con, con:
            if new is not None:
                con.execute("INSERT OR IGNORE INTO intents VALUES (?, ?, ?, ?, ?)", (fingerprint, *new))
            row = con.execute("SELECT job,cap,browser,epoch FROM intents WHERE fingerprint=?", (fingerprint,)).fetchone()
        return row


@dataclass
class Pending:
    state: str
    verifier: str
    redirect: str
    client_id: str
    expires: float


@dataclass
class Binding:
    browser: str
    epoch: str
    session: Session


class Runtime:
    def __init__(self, server, *, transport=None, journal=None):
        self.server = server
        self.transport = transport or Transport()
        self.journal = journal or Journal(Path(folder_paths.get_user_directory()) / "cormak_nodes" / "requests.sqlite3")
        self.pending = {}
        self.connecting = set()
        self.sessions = {}
        self.tickets = {}
        self.errors = {}
        self.connect_times = deque()
        self.request_times = {}
        self._tickets_lock = threading.Lock()
        self.public_origin = os.environ.get("CORMAK_PUBLIC_ORIGIN", "").strip()
        self.enabled = not (args.multi_user or args.offline) and all(
            host.strip() in ("localhost", "127.0.0.1", "::1") for host in args.listen.split(",")
        )
        if self.public_origin:
            try:
                origin = urlsplit(self.public_origin)
                self.enabled &= bool(origin.scheme == "https" and origin.hostname and not (
                    origin.username or origin.password or origin.path or origin.query or origin.fragment
                ) and origin.port in (None, 443) and origin.netloc == origin.hostname)
            except ValueError:
                self.enabled = False

    def origin(self, request):
        try:
            peer = request.transport.get_extra_info("peername") if request.transport else None
            if not self.enabled or not peer or not ipaddress.ip_address(peer[0]).is_loopback:
                return None
            host = request.host
            if self.public_origin:
                return self.public_origin if host == urlsplit(self.public_origin).netloc else None
            parsed = urlsplit("http://" + host)
            if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                return None
            if host not in (parsed.hostname, f"{parsed.hostname}:{parsed.port}",
                            "[::1]", f"[::1]:{parsed.port}"):
                return None
            return "http://" + host
        except (TypeError, ValueError):
            return None

    def browser(self, request):
        value = request.cookies.get(COOKIE, "")
        if len(value) == 43 and all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
            return digest(value)
        return None

    def _protected(self, request):
        origin = self.origin(request)
        if (not origin or request.headers.get("X-Cormak-Request") != "1"
                or request.headers.get("Origin", origin) != origin
                or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
            raise CormakError("unsupported_origin")
        return origin

    def _limit(self, browser, operation, limit, period):
        now = time.monotonic()
        for key, queue in list(self.request_times.items()):
            if queue and queue[-1] <= now - 60:
                del self.request_times[key]
        if len(self.request_times) >= 512 and (browser, operation) not in self.request_times:
            raise CormakError("rate_limit_reached")
        queue = self.request_times.setdefault((browser, operation), deque())
        while queue and queue[0] <= now - period:
            queue.popleft()
        if len(queue) >= limit:
            raise CormakError("rate_limit_reached")
        queue.append(now)

    def _json(self, payload, status=200):
        return web.json_response(payload, status=status, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        })

    async def handle(self, request):
        try:
            origin = self._protected(request)
            return await self._handle(request, origin)
        except CormakError as exc:
            return self._json({"error": str(exc), "code": exc.code}, 403 if exc.code == "unsupported_origin" else 400)
        except (OSError, sqlite3.Error):
            return self._json({"error": "Cormak could not access its local state."}, 503)

    async def _handle(self, request, origin):
        action = request.match_info["action"]
        browser = self.browser(request)
        current = self.sessions.get(browser)
        if action == "state" and request.method == "GET":
            self._limit(browser, "state", 120, 60)
            pending = self.pending.get(browser)
            return self._json({
                "connected": bool(current and current.session.connected),
                "pending": bool(pending and pending.expires > time.monotonic()) or browser in self.connecting,
                "approvals": dict(current.session.approvals) if current and current.session.connected else {},
                "error": self.errors.get(browser),
            })
        if request.method != "POST":
            return self._json({"error": "Method not allowed."}, 405)
        if action == "connect":
            return await self.connect(request, origin)
        if action == "disconnect":
            self._limit(browser, "disconnect", 8, 60)
            if browser in self.connecting:
                raise CormakError("connection_busy")
            self.pending.pop(browser, None)
            self.errors.pop(browser, None)
            if current:
                await current.session.disconnect()
                self.sessions.pop(browser, None)
            return self._json({"connected": False})
        if not current or not current.session.connected:
            raise CormakError("session_expired")
        if action == "account":
            self._limit(browser, "account", 12, 60)
            return self._json(await account(current.session))
        if action == "quote":
            self._limit(browser, "quote", 16, 60)
            try:
                body = await request.json()
                quality = body.get("quality") if isinstance(body, dict) else None
            except ValueError:
                quality = None
            if quality not in QUALITIES:
                raise CormakError("invalid_request")
            price, advance = await quote(current.session, quality)
            return self._json({"maximum": price, "advance": advance})
        return self._json({"error": "Unknown Cormak action."}, 404)

    async def connect(self, request, origin):
        incoming = self.browser(request)
        token = request.cookies.get(COOKIE) if incoming else secrets.token_urlsafe(32)
        browser = digest(token)
        self._limit(incoming, "connect", 6, 60)
        if self.sessions.get(browser) and self.sessions[browser].session.connected:
            raise CormakError("connection_busy")
        if browser in self.connecting:
            raise CormakError("connection_busy")
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value.expires > now}
        self.errors.pop(browser, None)
        self.sessions = {key: value for key, value in self.sessions.items() if value.session.connected}
        while self.connect_times and self.connect_times[0] < now - 60:
            self.connect_times.popleft()
        if len(self.connect_times) >= 12 or len(self.sessions) + len(self.pending) + len(self.connecting) >= 64:
            raise CormakError("registration_limited")
        self.connect_times.append(now)
        self.connecting.add(browser)
        try:
            redirect = origin + CALLBACK
            client_id = self.journal.client(browser, redirect)
            if not client_id:
                client_id = await register(self.transport, redirect)
                client_id = self.journal.client(browser, redirect, client_id)
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            state = secrets.token_urlsafe(32)
            self.pending[browser] = Pending(state, verifier, redirect, client_id, now + 600)
            response = self._json({"url": authorize_url(client_id, redirect, state, challenge)})
            response.set_cookie(COOKIE, token, max_age=90 * 86400, path="/", httponly=True,
                                samesite="Lax", secure=origin.startswith("https:"))
            return response
        finally:
            self.connecting.remove(browser)

    async def callback(self, request):
        origin = self.origin(request)
        browser = self.browser(request)
        pending = self.pending.get(browser)
        state = request.query.get("state", "")
        if (not origin or not pending or pending.redirect != origin + CALLBACK
                or pending.expires <= time.monotonic() or browser in self.connecting
                or len(state) != 43 or not hmac.compare_digest(state.encode("utf-8"), pending.state.encode())):
            return web.Response(text="Invalid or expired Cormak sign-in. Return to ComfyUI and start again.",
                                status=400, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        del self.pending[browser]
        self.connecting.add(browser)
        code = request.query.get("code", "")
        try:
            if not CODE.fullmatch(code):
                raise CormakError("invalid_grant")
            session = Session(pending.client_id, self.transport)
            await session.exchange(code, pending.verifier, pending.redirect)
            self.sessions[browser] = Binding(browser, uuid4().hex, session)
        except CormakError as exc:
            self.errors[browser] = str(exc)
            if len(self.errors) > 64:
                self.errors.pop(next(iter(self.errors)))
        finally:
            self.connecting.remove(browser)
        return web.HTTPSeeOther("/", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @web.middleware
    async def queue_middleware(self, request, handler):
        # Route middleware provides HTTP identity; the on-prompt hook supplies a
        # server-minted ID. The browser cookie never becomes a graph input.
        if request.method != "POST" or request.path not in QUEUE_PATHS:
            return await handler(request)
        try:
            body = await request.json()
        except (ValueError, web.HTTPException):
            return await handler(request)
        if not isinstance(body, dict):
            return await handler(request)

        graph = body.get("prompt")
        node_ids = frozenset(str(key) for key, node in graph.items()
                             if isinstance(node, dict) and isinstance(node.get("class_type"), str)
                             and node["class_type"] in NODE_IDS) if isinstance(graph, dict) else frozenset()
        custom_id = body.get("prompt_id")
        with self._tickets_lock:
            if isinstance(custom_id, str) and custom_id in self.tickets:
                return self._queue_error("That prompt ID is already in use.")
        if not node_ids:
            return await handler(request)

        origin = self.origin(request)
        if (not origin or request.headers.get("Origin", origin) != origin
                or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
            return self._queue_error("Cormak needs a private single-user ComfyUI instance on loopback or HTTPS.")
        browser = self.browser(request)
        binding = self.sessions.get(browser)
        # Allow the read-only Account node to report a disconnected state.
        if (not binding or not binding.session.connected) and any(
            graph[node]["class_type"] != "CormakAccount" for node in node_ids
        ):
            return self._queue_error("Connect Cormak in the sidebar before running 3D nodes.", 401)
        prompt_id = str(uuid4())
        with self._tickets_lock:
            now = time.monotonic()
            self.tickets = {key: value for key, value in self.tickets.items() if value[2] > now}
            if len(self.tickets) >= 4096:
                return self._queue_error("There are too many Cormak workflow requests. Try again later.", 429)
            self.tickets[prompt_id] = (binding, node_ids, now + TICKET_TTL)
        context_token = _browser_from_request.set(prompt_id)
        try:
            response = await handler(request)
            if response.status >= 400:
                with self._tickets_lock:
                    self.tickets.pop(prompt_id, None)
            return response
        finally:
            _browser_from_request.reset(context_token)

    def _queue_error(self, message, status=403):
        return self._json({"error": {"type": "cormak_connection", "message": message,
                                     "details": "", "extra_info": {}}, "node_errors": {}}, status)

    def on_prompt(self, data):
        prompt_id = _browser_from_request.get()
        if prompt_id:
            data["prompt_id"] = prompt_id
        return data

    def current(self, *, required=True):
        ctx = get_executing_context()
        with self._tickets_lock:
            ticket = self.tickets.get(ctx.prompt_id) if ctx else None
        if ticket and ctx.node_id in ticket[1] and ticket[2] > time.monotonic():
            binding = ticket[0]
            if binding and binding.session.connected and self.sessions.get(binding.browser) is binding:
                return binding
        if required:
            raise CormakError("session_expired")
        return None

    def install(self):
        self.server.app.middlewares.append(self.queue_middleware)
        self.server.add_on_prompt_handler(self.on_prompt)
        self.server.routes.get(CALLBACK)(self.callback)
        self.server.routes.get("/cormak/{action:state}")(self.handle)
        self.server.routes.post("/cormak/{action:connect|disconnect|account|quote}")(self.handle)


async def account(session):
    value = await session.call("get_account")
    balance = value.get("available_ai_credits")
    if type(balance) is not int or balance < 0:
        raise CormakError("transport_error")
    plan = value.get("plan", "")
    if not isinstance(plan, str) or not plan.replace("_", "").replace("-", "").isalnum() or len(plan) > 24:
        plan = ""
    return {"connected": True, "credits": balance, "plan": plan}


async def quote(session, quality):
    value = await session.call("quote_3d", quality=quality)
    quote_value, spending = value.get("quote"), value.get("spending")
    cost = quote_value.get("maximum") if isinstance(quote_value, dict) else None
    if (type(cost) is not int or not 0 < cost <= 1_000_000 or quote_value.get("quality") != quality
            or quote_value.get("currency") != "ai_credits"):
        raise CormakError("transport_error")
    return cost, bool(isinstance(spending, dict) and spending.get("starts_without_approval") is True)
