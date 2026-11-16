"""The public Cormak MCP OAuth and 3D contracts. No provider credentials."""
import asyncio
import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

import aiohttp

RESOURCE = "https://mcp.cormak.app/mcp"
APP = "https://app.cormak.app"
SITE = "https://cormak.app"
SCOPES = "account:read studio3d:use studio3d:read"
STORAGE = "csjjypcilykmdfiznjoc.supabase.co"
QUALITIES = ("basic", "medium", "high")
MODEL = "Meshy 7.1"
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
CODE = re.compile(r"cmcp_c_[A-Za-z0-9_-]{43}")
TRACK = "utm_source=comfyui&utm_medium=integration&utm_campaign=cormak_nodes"
LINKS = {
    "register": f"{APP}/3d?{TRACK}&utm_content=create_account",
    "credits": f"{APP}/billing?{TRACK}&utm_content=manage_credits",
    "site": f"{SITE}/?{TRACK}&utm_content=open_cormak",
    "connections": f"{APP}/mcp?{TRACK}&utm_content=connections",
    "docs": f"{SITE}/blog/create-3d-model-with-ai?{TRACK}&utm_content=documentation",
}

# Never include upstream bodies, URLs, or exception messages in errors or logs.
MESSAGES = {
    "session_expired": "Connect Cormak in the sidebar and retry the same workflow.",
    "scope_required": "Reconnect Cormak and allow all three requested permissions.",
    "rate_limit_reached": "Cormak is rate limiting requests. Wait one minute, then retry.",
    "connection_busy": "Another Cormak request is finishing. Try again shortly.",
    "insufficient_credits": f"Not enough Cormak AI credits. Manage credits: {LINKS['credits']}",
    "invalid_prompt": "Enter a description of 10–600 characters.",
    "invalid_image": "Connect one RGB image from Load Image or another IMAGE node.",
    "quote_changed": "Review the current quote in the Cormak sidebar and update the credit cap.",
    "studio_3d_failed": "Cormak could not finish this 3D model.",
    "content_blocked": "Cormak declined this content.",
    "ai_consent_required": f"Accept Cormak’s AI processing terms: {APP}/3d",
    "revocation_unconfirmed": f"Disconnected locally. Revoke the connection in Cormak: {LINKS['connections']}",
    "invalid_glb": "Cormak did not return a valid self-contained GLB within the 100 MB limit.",
    "transport_error": "Cormak did not respond. Retry the same workflow and keep its run number.",
    "approval_required": "Approve this request in the Cormak sidebar and run the same workflow again.",
    "approval_expired": "Cormak approval expired. Set a new run number to request a new generation.",
    "denied": "This generation was declined in Cormak.",
    "studio_3d_not_found": "This job is not available through the current connection.",
    "idempotency_conflict": "Cormak rejected a mismatched retry. Check the original job in Cormak.",
    "result_unknown": "Cormak could not confirm the outcome. Check its status and keep the same run number.",
    "generation_limit_reached": "This Cormak connection reached its generation limit.",
    "permission_denied": "Cormak denied access. Review your connection and permissions.",
    "invalid_grant": "Cormak authorization expired. Start the connection again.",
    "approval_limit_reached": "Resolve pending approvals in Cormak before starting more jobs.",
    "asset_denied": "The private GLB link is unavailable. Retry the same job to refresh it.",
    "unsupported_origin": "Use a dedicated ComfyUI instance on loopback or a private HTTPS proxy.",
    "invalid_request": "Invalid Cormak request.",
    "registration_limited": "Too many connection attempts. Wait a minute and try again.",
    "invalid_asset_path": "Cannot safely save into the Cormak output folder.",
    "session_mismatch": "This run belongs to another browser session. Reconnect the original account or explicitly choose a new run number.",
}


class CormakError(RuntimeError):
    def __init__(self, code, detail=""):
        self.code = code
        super().__init__(MESSAGES.get(code, "Cormak could not complete this request.") + detail)


def approval_link(value):
    if not isinstance(value, str):
        return None
    return value if re.fullmatch(re.escape(APP) + r"/mcp\?approval_id=" + UUID.pattern, value) else None


def asset_link(value):
    if not isinstance(value, str) or len(value) > 6000:
        raise CormakError("invalid_glb")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise CormakError("invalid_glb") from None
    if not (
        parsed.scheme == "https" and parsed.netloc == STORAGE and not parsed.fragment
        and re.fullmatch(r"/storage/v1/object/sign/studio-models/[a-zA-Z0-9_./-]+\.glb", parsed.path)
        and all(part not in ("", ".", "..") for part in parsed.path.split("/")[6:])
        and re.fullmatch(r"token=[A-Za-z0-9_.%-]+", parsed.query)
    ):
        raise CormakError("invalid_glb")
    return value


class Transport:
    async def request(self, url, *, payload=None, form=None, headers=None, empty=False):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as http:
                async with http.post(url, json=payload, data=form, headers=headers, allow_redirects=False) as resp:
                    if resp.status == 429:
                        raise CormakError("rate_limit_reached")
                    if resp.status == 401:
                        return 401, {}
                    if resp.status >= 500:
                        raise CormakError("transport_error")
                    if empty and resp.status == 200:
                        return resp.status, {}
                    body = bytearray()
                    async for chunk in resp.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > 128_000:
                            raise CormakError("transport_error")
                    if resp.content_type == "text/event-stream":
                        # Cormak's stateless MCP endpoint may send a single SSE event.
                        for line in body.splitlines():
                            if line.startswith(b"data:"):
                                reply = json.loads(line[5:])
                                if isinstance(reply, dict) and reply.get("id") == 1:
                                    return resp.status, reply
                        raise CormakError("transport_error")
                    return resp.status, json.loads(body)
        except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError):
            raise CormakError("transport_error") from None


class Session:
    """One browser's OAuth grant. Rotation and disconnect are serialized across Comfy's loops."""
    def __init__(self, client_id, transport):
        self.client_id = client_id
        self.http = transport
        self._access = self._refresh = ""
        self._expiry = 0.0
        self._lock = threading.Lock()
        self.approvals = {}

    @property
    def connected(self):
        return bool(self._refresh)

    async def _acquire(self):
        until = time.monotonic() + 30
        while not self._lock.acquire(blocking=False):
            if time.monotonic() >= until:
                raise CormakError("connection_busy")
            await asyncio.sleep(0.05)

    async def _token(self, **form):
        status, reply = await self.http.request(
            RESOURCE + "/token", form={**form, "client_id": self.client_id, "resource": RESOURCE},
        )
        if not isinstance(reply, dict):
            raise CormakError("invalid_grant")
        access, refresh = reply.get("access_token"), reply.get("refresh_token")
        expires, scope = reply.get("expires_in"), reply.get("scope")
        if (status != 200 or not isinstance(access, str) or not isinstance(refresh, str)
                or not re.fullmatch(r"cmcp_a_[A-Za-z0-9_-]{43}", access)
                or not re.fullmatch(r"cmcp_r_[A-Za-z0-9_-]{43}", refresh)
                or type(expires) is not int or expires <= 0):
            raise CormakError("invalid_grant")
        self._access, self._refresh = access, refresh
        self._expiry = time.monotonic() + expires
        if not isinstance(scope, str) or set(scope.split()) != set(SCOPES.split()):
            raise CormakError("scope_required")

    async def exchange(self, code, verifier, redirect):
        try:
            await self._token(grant_type="authorization_code", code=code, code_verifier=verifier,
                              redirect_uri=redirect)
        except CormakError:
            if self.connected:
                await self.disconnect()
            raise

    async def _rotate(self):
        try:
            await self._token(grant_type="refresh_token", refresh_token=self._refresh)
        except (CormakError, asyncio.CancelledError):
            # A refresh may have committed even when its response was lost.
            self._forget()
            raise CormakError("session_expired") from None

    def _forget(self):
        self._access = self._refresh = ""
        self.approvals.clear()

    async def disconnect(self):
        await self._acquire()
        try:
            token = self._refresh
            self._forget()
            if token:
                status, _ = await self.http.request(RESOURCE + "/revoke", empty=True, form={
                    "client_id": self.client_id, "resource": RESOURCE, "token": token,
                })
                if status != 200:
                    raise CormakError("revocation_unconfirmed")
        except CormakError:
            raise CormakError("revocation_unconfirmed") from None
        finally:
            self._lock.release()

    async def call(self, tool, **arguments):
        await self._acquire()
        try:
            if not self.connected:
                raise CormakError("session_expired")
            if self._expiry <= time.monotonic() + 20:
                await self._rotate()
            for attempt in range(2):
                status, reply = await self.http.request(
                    RESOURCE, headers={
                        "Authorization": "Bearer " + self._access,
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-11-25",
                    },
                    payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": tool, "arguments": arguments}},
                )
                if status == 401:
                    if attempt == 0:
                        await self._rotate()
                        continue
                    self._forget()
                    raise CormakError("session_expired")
                if status != 200 or not isinstance(reply, dict):
                    raise CormakError("permission_denied" if status == 403 else "transport_error")
                if reply.get("id") != 1 or reply.get("jsonrpc") != "2.0":
                    raise CormakError("transport_error")
                result = reply.get("result")
                value = result.get("structuredContent") if isinstance(result, dict) else None
                if not isinstance(value, dict):
                    raise CormakError("transport_error")
                # Failed jobs may carry status/credit data and isError together.
                if result.get("isError") and not value.get("status"):
                    error = value.get("error")
                    if error in ("studio_3d_status_failed", "studio_3d_unavailable", "generation_unavailable"):
                        raise CormakError("transport_error")
                    raise CormakError(error if isinstance(error, str) and error in MESSAGES else "remote_error")
                return value
        finally:
            self._lock.release()


async def register(transport, redirect):
    status, reply = await transport.request(RESOURCE + "/register", payload={
        "client_name": "Cormak Nodes for ComfyUI", "redirect_uris": [redirect],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
    })
    client_id = reply.get("client_id") if isinstance(reply, dict) else None
    if status != 201 or not isinstance(client_id, str) or not UUID.fullmatch(client_id):
        raise CormakError("transport_error")
    return client_id


def authorize_url(client_id, redirect, state, challenge):
    return RESOURCE + "/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "state": state, "code_challenge": challenge,
        "response_type": "code", "resource": RESOURCE, "scope": SCOPES, "code_challenge_method": "S256",
    })
