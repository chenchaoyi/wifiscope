"""Runtime glue: build a sink from saved pairing, drive periodic flush,
and render the status chip.

Activation is opt-in by pairing: if no pairing-state file exists (or
``DITING_COMPANION=0``), :func:`build_sink` returns ``None`` and callers
stay completely inert — nothing is imported from the crypto stack and no
egress happens. So the hot TUI / monitor path pays nothing until the user
runs ``diting companion pair``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..i18n import t

if TYPE_CHECKING:  # avoid importing the crypto stack at module load
    from .sink import CompanionSink

FLUSH_INTERVAL_S = 3.0
# Consecutive fully-failed flushes before the chip names the outage —
# ~9 s at the flush interval, so a transient blip never flashes it.
UNREACHABLE_AFTER_FAILURES = 3
# Envelopes sent per flush. Bounds the per-call blocking time on a slow link
# so a large backlog drains incrementally across periodic cycles instead of
# one all-or-nothing burst that blocks the flush thread for minutes.
DEFAULT_FLUSH_BATCH = 50


def _state_path_if_paired(state_path: Path | None) -> Path | None:
    """Resolve the pairing-state path and return it only if the file
    exists — a cheap check that avoids importing the crypto stack (and
    pynacl) on the common unpaired path."""
    if os.environ.get("DITING_COMPANION") == "0":
        return None
    if state_path is not None:
        path = state_path
    else:
        override = os.environ.get("DITING_COMPANION_STATE")
        path = Path(override).expanduser() if override else Path("diting-companion.json")
    return path if path.exists() else None


def build_sink(state_path: Path | None = None) -> "CompanionSink | None":
    """Build a CompanionSink from saved pairing, or None if not paired."""
    path = _state_path_if_paired(state_path)
    if path is None:
        return None
    # Heavy imports happen only when actually paired.
    from .push_policy import PushPolicy
    from .relay_client import RelayClient
    from .sink import CompanionSink
    from .state import load_state

    st = load_state(path)
    if st is None:
        return None
    client = RelayClient(st.relay_url, st.channel, st.relay_token())
    return CompanionSink(st, client, PushPolicy(), state_path=path)


async def flush_loop(sink: "CompanionSink", *, interval: float = FLUSH_INTERVAL_S) -> None:
    """Periodically drain the relay queue off the event loop. On cancel,
    make a best-effort final drain so a clean shutdown isn't lossy."""
    try:
        while True:
            await asyncio.sleep(interval)
            if sink.client.pending:
                await asyncio.to_thread(sink.flush, DEFAULT_FLUSH_BATCH)
    except asyncio.CancelledError:
        if sink.client.pending:
            try:
                # Best-effort, bounded: a clean quit never hangs the shutdown
                # draining a deep backlog over a slow link.
                await asyncio.to_thread(sink.flush, DEFAULT_FLUSH_BATCH)
            except Exception:
                pass
        raise


# The command loop ticks ~1 s for commands + liveness; frames stream on
# their own pump thread, not this tick.
CAMERA_POLL_INTERVAL_S = 1.0

# Preview stream defaults — a moderate size + ~4 fps keep relay/bandwidth
# reasonable while the camera-open-once stream stays smooth.
CAMERA_STREAM_INTERVAL_S = 0.25
CAMERA_STREAM_WIDTH = 960
CAMERA_STREAM_HEIGHT = 540
CAMERA_STREAM_QUALITY = 0.5


def _default_open_stream():
    """Open the helper's live `camstream`, or None when the helper is
    missing/old or the camera is denied. Imported lazily so the crypto/helper
    stack stays off the unpaired path."""
    from .. import _helper

    binary = _helper.find_helper()
    if not binary:
        return None
    try:
        return _helper.camstream(
            binary,
            interval=CAMERA_STREAM_INTERVAL_S,
            width=CAMERA_STREAM_WIDTH,
            height=CAMERA_STREAM_HEIGHT,
            quality=CAMERA_STREAM_QUALITY,
        )
    except OSError:
        return None


def enable_camera(state_path=None):
    """Grant + enable the remote camera in the FOREGROUND: run one helper
    `camsnap`, which surfaces the macOS camera prompt where the user can
    approve it (a background daemon can't), and only flip the flag on if the
    grant resolves. Returns ``(ok, status, frame)``; ``status`` is one of
    ``ok`` / ``not_paired`` / ``no_helper`` / ``unsupported`` / ``denied`` /
    ``restricted`` / ``not_determined`` / ``error``. Shared by the CLI and
    the TUI so the grant flow is identical from either surface."""
    from .. import _helper
    from .state import load_state

    st = load_state(state_path)
    if st is None:
        return False, "not_paired", None
    binary = _helper.find_helper()
    if not binary:
        return False, "no_helper", None
    frame, status = _helper.camsnap(binary)
    if status != "ok" or frame is None:
        return False, status, None
    st.camera_enabled = True
    st.save(state_path)
    return True, "ok", frame


def disable_camera(state_path=None) -> bool:
    """Turn the remote camera capability off. Returns False if not paired."""
    from .state import load_state

    st = load_state(state_path)
    if st is None:
        return False
    st.camera_enabled = False
    st.save(state_path)
    return True


def make_camera_driver(sink: "CompanionSink", *, open_stream=None):
    """Build a remote-camera session driver for ``sink``, or None when the
    camera capability is off. Used by the interactive TUI, which ticks the
    driver on its own timer (the headless daemon uses ``command_poll_loop``
    instead)."""
    if not sink.camera_enabled:
        return None
    from .camera import CameraSessionDriver

    return CameraSessionDriver(sink, open_stream or _default_open_stream)


async def command_poll_loop(
    sink: "CompanionSink",
    *,
    open_stream=None,
    interval: float = CAMERA_POLL_INTERVAL_S,
) -> None:
    """Drive the remote-camera session off the event loop. Each tick drains
    sealed commands and honours the liveness timeout (run in a worker thread
    so the blocking GET never stalls the loop); frames are forwarded by the
    session's own pump thread."""
    from .camera import CameraSessionDriver

    driver = CameraSessionDriver(sink, open_stream or _default_open_stream)
    try:
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(driver.tick)
    except asyncio.CancelledError:
        raise


def subtitle_chip(sink: "CompanionSink") -> str:
    """Short companion status for the TUI header subtitle."""
    c = sink.client
    if c.pending and c.dropped:
        chip = t("companion: {n} queued, {d} dropped", n=c.pending, d=c.dropped)
    elif c.pending:
        chip = t("companion: {n} queued", n=c.pending)
    elif c.dropped:
        return t("companion: {d} dropped", d=c.dropped)
    else:
        return t("companion: on")
    # A queued backlog reads very differently depending on whether delivery
    # is merely behind or failing outright — name a sustained outage.
    if c.consecutive_failures >= UNREACHABLE_AFTER_FAILURES:
        chip += t(" · relay unreachable")
    return chip
