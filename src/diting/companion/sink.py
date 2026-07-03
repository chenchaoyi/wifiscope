"""CompanionSink — the join point.

Given a wire payload dict (the exact dict the JSONL writer emits), decide
push-worthiness (PushPolicy), seal it under the channel key (crypto), and
enqueue it on the relay client. ``offer`` is cheap and non-blocking so it
is safe to call from the TUI / monitor event loop; a separate periodic
``flush`` (driven by the wiring layer) drains the queue to the relay.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .crypto import open_command, seal_event, seal_media
from .protocol.apns import coarse_category
from .protocol.errors import ProtocolError
from .push_policy import PushPolicy
from .push_summary import push_summary
from .relay_client import RelayClient
from .protocol.events_schema import LOCAL_ONLY_FIELDS as _LOCAL_ONLY_FIELDS
from .state import PairingState


class CompanionSink:
    def __init__(
        self,
        state: PairingState,
        client: RelayClient,
        policy: PushPolicy,
        *,
        state_path: Path | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._state = state
        self._client = client
        self._policy = policy
        self._state_path = state_path
        self._monotonic = monotonic
        self._key = state.key_bytes()

    @property
    def client(self) -> RelayClient:
        return self._client

    @property
    def camera_enabled(self) -> bool:
        return self._state.camera_enabled

    # ---------- remote-camera control/media plane ----------
    # Command/media ride the same channel key + monotonic seq cursor as
    # events, but are a separate plane: they never go through offer()/the
    # event log. The key stays encapsulated here.

    def drain_commands(self) -> list[dict[str, Any]]:
        """Pull + open the pending phone→desktop camera commands. Envelopes
        that fail authentication (tamper / wrong key) are dropped, never
        surfaced."""
        out: list[dict[str, Any]] = []
        for env in self._client.poll_commands():
            try:
                out.append(open_command(self._key, env))
            except ProtocolError:
                pass
        return out

    def send_frame(self, frame: dict[str, Any]) -> int:
        """Seal one still frame under the channel key on the shared monotonic
        seq cursor and POST it to the ephemeral media route. Returns the HTTP
        status."""
        seq = self._state.next_seq(self._state_path)
        envelope = seal_media(
            self._key,
            channel=self._state.channel,
            seq=seq,
            ts=datetime.now().astimezone().isoformat(),
            frame=frame,
        )
        return self._client.post_media(envelope)

    def offer(self, payload: dict[str, Any]) -> bool:
        """Consider one wire payload for forwarding. Returns True if it was
        sealed and enqueued, False if the policy declined it."""
        if not self._policy.should_push(payload, self._monotonic()):
            return False
        if any(k in payload for k in _LOCAL_ONLY_FIELDS):
            payload = {
                k: v for k, v in payload.items() if k not in _LOCAL_ONLY_FIELDS
            }
        seq = self._state.next_seq(self._state_path)
        envelope = seal_event(
            self._key,
            channel=self._state.channel,
            seq=seq,
            ts=datetime.now().astimezone().isoformat(),
            payload=payload,
        )
        self._client.enqueue(
            envelope,
            category=coarse_category(payload.get("type", "")),
            summary=push_summary(payload),
        )
        return True

    def flush(self, max_batch: int | None = None):
        return self._client.flush(max_batch)
