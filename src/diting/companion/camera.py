"""Desktop remote-camera session driver.

Drives a camera session from the phone's sealed commands: drain
`camera.start` / `camera.keepalive` / `camera.stop`, and while a session is
open, run a live camera *stream* (the helper's `camstream` — camera opens
once, warms up once) and forward each frame sealed over the media plane.
Streaming avoids the per-frame cold-start of one-shot `camsnap`, so the phone
sees a smooth preview.

Fail-safe: a session with no keepalive within the liveness timeout
auto-stops, so a vanished phone (killed / backgrounded / offline) cannot
hold the camera open. Replay-safe: each command's `cmd_id` is single-use
and its `exp` must be in the future.

`tick()` (commands + liveness) is pure and synchronous — the async loop in
``runtime.py`` drives it off the event loop via ``asyncio.to_thread`` — while
frame forwarding runs on a daemon pump thread started/stopped by the session.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

# The desktop stops an unattended session after this long without a
# keepalive/stop; the phone beats well inside it.
LIVENESS_TIMEOUT_S = 30.0


class _Sink(Protocol):
    def drain_commands(self) -> list[dict[str, Any]]: ...
    def send_frame(self, frame: dict[str, Any]) -> int: ...


class _Stream(Protocol):
    def frames(self): ...
    def close(self) -> None: ...


# open_stream() -> a live camera stream, or None when the camera can't be
# opened (missing/old helper, TCC denied). A None stream ends the session.
OpenStream = Callable[[], "_Stream | None"]


def _parse_exp(exp: object) -> datetime | None:
    """Parse an ISO-8601 expiry with a numeric offset; None if malformed or
    naive (a tz-naive expiry can't be compared safely, so treat as stale)."""
    if not isinstance(exp, str):
        return None
    try:
        parsed = datetime.fromisoformat(exp)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class CameraSessionDriver:
    def __init__(
        self,
        sink: _Sink,
        open_stream: OpenStream,
        *,
        now: Callable[[], datetime] | None = None,
        liveness_timeout: float = LIVENESS_TIMEOUT_S,
    ) -> None:
        self._sink = sink
        self._open_stream = open_stream
        self._now = now or (lambda: datetime.now().astimezone())
        self._liveness_timeout = liveness_timeout
        self._active = False
        self._last_seen: datetime | None = None
        self._seen_ids: set[str] = set()
        self._frame_seq = 0
        self._stream: _Stream | None = None
        self._pump: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def frame_seq(self) -> int:
        return self._frame_seq

    def tick(self) -> None:
        """One driver step: apply pending commands and honour the liveness
        timeout. Frame forwarding happens on the pump thread, not here."""
        now = self._now()
        for cmd in self._sink.drain_commands():
            self._handle(cmd, now)
        if (
            self._active
            and self._last_seen is not None
            and (now - self._last_seen).total_seconds() > self._liveness_timeout
        ):
            self._stop("liveness timeout")

    def _handle(self, cmd: dict[str, Any], now: datetime) -> None:
        cmd_id = cmd.get("cmd_id")
        name = cmd.get("cmd")
        if not isinstance(cmd_id, str) or cmd_id in self._seen_ids:
            return  # malformed / replayed — ignore
        exp = _parse_exp(cmd.get("exp"))
        if exp is None or exp < now:
            return  # stale / unparseable — ignore
        self._seen_ids.add(cmd_id)
        if name == "camera.start":
            if self._active:
                self._last_seen = now
            else:
                self._start(now)
        elif name == "camera.keepalive":
            if self._active:
                self._last_seen = now
        elif name == "camera.stop":
            if self._active:
                self._stop("stop command")

    def _start(self, now: datetime) -> None:
        stream = self._open_stream()
        if stream is None:
            log.warning("remote-camera: cannot open the camera stream — session not started")
            return
        self._active = True
        self._last_seen = now
        self._frame_seq = 0
        self._stream = stream
        self._pump = threading.Thread(
            target=self._run_pump, args=(stream,), name="camera-pump", daemon=True
        )
        self._pump.start()
        log.info("remote-camera session started")

    def _run_pump(self, stream: _Stream) -> None:
        """Forward streamed frames to the media plane until the session stops
        (the stream is closed → its frame iterator ends)."""
        try:
            for frame in stream.frames():
                if not self._active:
                    break
                self._frame_seq += 1
                self._sink.send_frame({**frame, "seq": self._frame_seq})
        except Exception:  # noqa: BLE001 — a pump error must not crash the daemon
            log.warning("remote-camera pump ended on error", exc_info=True)

    def _stop(self, reason: str) -> None:
        self._active = False
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()  # ends the pump's frame iterator
        log.info(
            "remote-camera session stopped (%s) after %d frames",
            reason,
            self._frame_seq,
        )
