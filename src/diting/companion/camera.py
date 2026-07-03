"""Desktop remote-camera session driver.

Drives a camera session from the phone's sealed commands: drain
`camera.start` / `camera.keepalive` / `camera.stop`, and while a session is
open, grab a still frame per interval via the helper and forward it sealed
over the media plane.

Fail-safe: a session with no keepalive within the liveness timeout
auto-stops, so a vanished phone (killed / backgrounded / offline) cannot
hold the camera open. Replay-safe: each command's `cmd_id` is single-use
and its `exp` must be in the future.

Pure synchronous logic (`tick()`); the async loop in ``runtime.py`` drives
it off the event loop via ``asyncio.to_thread``. No timers here, so it is
unit-testable by calling ``tick()`` directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

# The desktop stops an unattended session after this long without a
# keepalive/stop; the phone beats well inside it.
LIVENESS_TIMEOUT_S = 30.0
# Minimum spacing between captured frames (~the phone's snapshot cadence).
FRAME_INTERVAL_S = 1.5


class _Sink(Protocol):
    def drain_commands(self) -> list[dict[str, Any]]: ...
    def send_frame(self, frame: dict[str, Any]) -> int: ...


# capture() -> (frame | None, status). status in {ok, denied, restricted,
# not_determined, error}. A hard denial stops the session; a transient
# error just skips a frame.
Capture = Callable[[], "tuple[dict[str, Any] | None, str]"]


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
        capture: Capture,
        *,
        now: Callable[[], datetime] | None = None,
        liveness_timeout: float = LIVENESS_TIMEOUT_S,
        frame_interval: float = FRAME_INTERVAL_S,
    ) -> None:
        self._sink = sink
        self._capture = capture
        self._now = now or (lambda: datetime.now().astimezone())
        self._liveness_timeout = liveness_timeout
        self._frame_interval = frame_interval
        self._active = False
        self._last_seen: datetime | None = None
        self._last_frame_at: datetime | None = None
        self._seen_ids: set[str] = set()
        self._frame_seq = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def frame_seq(self) -> int:
        return self._frame_seq

    def tick(self) -> None:
        """One driver step: apply pending commands, honour the liveness
        timeout, and capture+forward a frame if one is due."""
        now = self._now()
        for cmd in self._sink.drain_commands():
            self._handle(cmd, now)
        if (
            self._active
            and self._last_seen is not None
            and (now - self._last_seen).total_seconds() > self._liveness_timeout
        ):
            self._stop("liveness timeout")
            return
        if self._active and (
            self._last_frame_at is None
            or (now - self._last_frame_at).total_seconds() >= self._frame_interval
        ):
            self._grab(now)

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
        self._active = True
        self._last_seen = now
        self._last_frame_at = None
        self._frame_seq = 0
        log.info("remote-camera session started")

    def _stop(self, reason: str) -> None:
        self._active = False
        log.info(
            "remote-camera session stopped (%s) after %d frames",
            reason,
            self._frame_seq,
        )

    def _grab(self, now: datetime) -> None:
        frame, status = self._capture()
        if status in ("denied", "restricted"):
            log.warning("remote-camera capture %s — stopping session", status)
            self._stop(f"capture {status}")
            return
        if frame is None:
            return  # transient error — retry next tick
        self._frame_seq += 1
        self._sink.send_frame({**frame, "seq": self._frame_seq})
        self._last_frame_at = now
