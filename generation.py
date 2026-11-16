"""Quotes, durable retry identity and asynchronous status polling."""
import asyncio
import json
import time
from uuid import uuid4

from .assets import download
from .client import CormakError, MESSAGES, MODEL, QUALITIES, UUID, approval_link
from .runtime import account, digest, quote

RUNNING = {"pending", "starting", "running", "queued", "processing", "in_progress"}
TERMINAL = {"failed", "unknown", "denied", "expired"}
APPROVAL = {"awaiting_confirmation", "approved"}
WAIT_SECONDS = 15 * 60
POLL_SECONDS = 10


def metadata(job, value):
    status = value.get("status")
    progress = value.get("progress")
    charged = value.get("credits_charged")
    error = value.get("error")
    return {
        "job_id": job, "status": status if isinstance(status, str) and status in
        (RUNNING | TERMINAL | APPROVAL | {"succeeded"}) else "unknown",
        "model": MODEL,
        "quality": value.get("quality") if value.get("quality") in QUALITIES else None,
        "progress": min(100, max(0, int(progress))) if type(progress) is int else 0,
        "credits_charged": charged if type(charged) is int else 0,
        "refunded": value.get("refunded") is True,
        **({"error": error} if isinstance(error, str) and error in MESSAGES else {}),
    }


def remember_approval(session, job, value):
    url = approval_link(value.get("approval_url"))
    if url:
        # Status may be retried in another tab of the same browser.
        if len(session.approvals) >= 10 and job not in session.approvals:
            session.approvals.pop(next(iter(session.approvals)), None)
        session.approvals[job] = url


async def status(session, job):
    if not isinstance(job, str) or not UUID.fullmatch(job):
        raise CormakError("invalid_request")
    value = await session.call("get_3d_status", request_id=job)
    if value.get("request_id") != job:
        raise CormakError("transport_error")
    if value.get("status") == "awaiting_confirmation":
        remember_approval(session, job, value)
    else:
        session.approvals.pop(job, None)
    return value


def fail_if_terminal(job, value):
    state = value.get("status")
    if state in TERMINAL:
        info = metadata(job, value)
        code = "approval_expired" if state == "expired" else (
            "result_unknown" if state == "unknown" else info.get("error", state))
        refunded = " Cormak reports a refund." if info["refunded"] else ""
        raise CormakError(code, f"{refunded} Job: {job}.")


async def download_result(runtime, session, job, value, root, *, prefix="model"):
    for attempt in range(2):
        fail_if_terminal(job, value)
        if value.get("status") != "succeeded":
            raise CormakError("result_unknown", f" Job: {job}.")
        try:
            path = await download(value.get("model_url"), root, job, prefix=prefix,
                                  asset_fetch=getattr(runtime, "asset_fetch", None))
            return path, metadata(job, value)
        except CormakError as exc:
            if exc.code != "asset_denied" or attempt:
                raise
            value = await status(session, job)


async def generate(runtime, binding, arguments, cap, run, *, check, progress):
    if type(run) is not int or not 0 <= run <= 2147483647 or type(cap) is not int or not 0 <= cap <= 1_000_000:
        raise CormakError("invalid_request")
    session = binding.session
    quality = arguments["quality"]
    # Keep the same ID for the same payload and run number, even after a Comfy restart.
    fingerprint = digest(json.dumps([binding.browser, 1, run, arguments], separators=(",", ":"), sort_keys=True))
    record = await asyncio.to_thread(runtime.journal.intent, fingerprint)
    if not record:
        maximum, _ = await quote(session, quality)
        if cap < maximum:
            raise CormakError("quote_changed", f" Current maximum: {maximum} AI credits.")
        if (await account(session))["credits"] < maximum:
            raise CormakError("insufficient_credits")
        record = await asyncio.to_thread(runtime.journal.intent, fingerprint, (
            str(uuid4()), maximum, binding.browser, binding.epoch))
    job, maximum, browser, epoch = record
    if browser != binding.browser:
        raise CormakError("session_mismatch", f" Job: {job}.")
    # Ask for status before any write. A reconnect can resume a server-known job
    # but must not turn an ambiguous or inaccessible old intent into a new charge.
    try:
        value = await status(session, job)
    except CormakError as exc:
        if exc.code != "studio_3d_not_found":
            raise
        if epoch != binding.epoch:
            raise CormakError("result_unknown", f" Job: {job}.") from None
        value = {"status": "not_started"}

    if value.get("status") in ("not_started", "approved"):
        if cap < maximum:
            raise CormakError("quote_changed", f" Original maximum: {maximum} AI credits.")
        check()
        value = await session.call("generate_3d", **arguments, request_id=job,
                                   confirm_spend=True, expected_max_credits=maximum)
        if value.get("request_id") != job:
            raise CormakError("transport_error")
    deadline = time.monotonic() + WAIT_SECONDS
    stalls = 0
    while True:
        check()
        state = value.get("status")
        if state == "succeeded":
            progress(100)
            # The generation's return is a receipt; status owns the signed URL.
            return job, value if value.get("model_url") else await status(session, job)
        if state == "awaiting_confirmation":
            remember_approval(session, job, value)
            raise CormakError("approval_required", f" Job: {job}.")
        fail_if_terminal(job, value)
        if state not in RUNNING or time.monotonic() >= deadline:
            raise CormakError("result_unknown", f" Job: {job}.")
        progress(metadata(job, value)["progress"])
        delay = POLL_SECONDS if stalls == 0 else min(40, POLL_SECONDS * (2 ** stalls))
        until = time.monotonic() + delay
        while time.monotonic() < until:
            check()
            await asyncio.sleep(min(0.5, max(0, until - time.monotonic())))
        try:
            value = await status(session, job)
            stalls = 0
        except CormakError as exc:
            if exc.code not in ("transport_error", "rate_limit_reached") or stalls >= 3:
                raise
            stalls += 1
