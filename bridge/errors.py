"""Plain-language messages with a next step for errors a user can hit (spec §6.6, §7.5, #22)."""

from __future__ import annotations

import modal.exception as mx

SETUP_HINT = "Open Extensions → Modal Cloud → Setup."


class BridgeError(Exception):
    """An error whose message is already written for the user."""


def friendly(exc: BaseException) -> str:
    """One or two sentences for a toast or dialog. Never a stack trace."""
    text = str(exc).strip()
    if isinstance(exc, BridgeError):
        return text
    lowered = text.lower()
    if isinstance(exc, mx.AuthError) or ("token" in lowered and ("invalid" in lowered or "missing" in lowered)):
        return f"Not signed in to Modal, or the sign-in has expired. {SETUP_HINT}"
    if isinstance(exc, mx.NotFoundError):
        return f"The cloud backend isn't deployed in your Modal account yet. {SETUP_HINT}"
    if "billing" in lowered or "payment" in lowered or "spend limit" in lowered or "credits" in lowered:
        return "Modal refused the job because of your account's billing or spending limit. Check the Modal billing page."
    if isinstance(exc, mx.ResourceExhaustedError) or "quota" in lowered:
        return "Modal has no free GPUs of this type right now, or your account's limit was reached. Try again, or pick another GPU in Settings → Modal Cloud."
    if isinstance(exc, (mx.TimeoutError, TimeoutError)):  # before OSError: TimeoutError is one
        return text or "Timed out waiting for Modal. Try again."
    if isinstance(exc, (mx.ConnectionError, ConnectionError)):
        return "Can't reach Modal. Check your internet connection and try again."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def friendly_execution_error(data: dict) -> str | None:
    """A hint for a cloud execution_error, or None if the node's own message says enough."""
    kind = f"{data.get('exception_type', '')} {data.get('exception_message', '')}".lower()
    if "outofmemory" in kind or "out of memory" in kind or "cuda oom" in kind:
        return "The cloud GPU ran out of memory. Pick a bigger GPU (A100-80GB or H100) in Settings → Modal Cloud, or lower the resolution or batch size."
    if "no such file" in kind or ("not found" in kind and "model" in kind):
        return "A file the workflow needs isn't in your Modal cloud. Upload it or pick another model."
    return None
