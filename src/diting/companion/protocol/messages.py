"""Non-event sealed message classes: `command` and `media` (remote camera).

These ride the SAME envelope and the SAME authenticated-encryption seal as
events (see ``crypto.seal_command`` / ``seal_media``), but they are NOT part
of the event vocabulary: they are never produced by ``EventLogger``, never
enter the durable report, and never appear in the events timeline. They
travel over the relay's ephemeral command / media routes only.

- ``command`` (phone → desktop): a control message driving a camera session.
  ``cmd`` names the action, ``cmd_id`` is unique per command, and ``exp`` is
  an ISO-8601 expiry so a replayed or stale command is rejected.
- ``media`` (desktop → phone): one sealed still frame — format, pixel
  dimensions, an in-session frame ``seq``, and the base64 image bytes.

Like ``events_schema``, this module is the single source for BOTH the runtime
validators (``validate_command`` / ``validate_media``) and the vendored JSON
Schemas (``build_command_schema`` / ``build_media_schema``) — one source, two
artifacts, no drift. The reproducibility + conformance tests guard the pair.
"""

from __future__ import annotations

from typing import Any

from ._schema_spec import TS_PATTERN
from .errors import ProtocolError
from .events_schema import _TS_RE

# The camera control vocabulary. `start` opens a session, `keepalive` holds
# it open (the phone beats while its viewer is foregrounded), `stop` closes
# it. Any other value fails closed.
CAMERA_COMMANDS: frozenset[str] = frozenset(
    {"camera.start", "camera.keepalive", "camera.stop"}
)

# The only frame format v1 emits. A future codec is an additive enum entry.
MEDIA_FORMATS: frozenset[str] = frozenset({"jpeg"})


def _pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def validate_command(obj: Any) -> dict[str, Any]:
    """Return ``obj`` if it is a well-formed camera command, else raise
    :class:`ProtocolError`. Unknown keys, missing required keys, a bad
    ``cmd`` enum, or a non-ISO ``exp`` all fail closed."""
    if not isinstance(obj, dict):
        raise ProtocolError(f"command must be an object, got {type(obj).__name__}")
    allowed = {"cmd", "cmd_id", "exp", "args"}
    unknown = set(obj) - allowed
    if unknown:
        raise ProtocolError(f"command: unknown field(s): {', '.join(sorted(unknown))}")
    cmd = obj.get("cmd")
    if cmd not in CAMERA_COMMANDS:
        raise ProtocolError(f"command 'cmd' not a known command: {cmd!r}")
    cmd_id = obj.get("cmd_id")
    if not isinstance(cmd_id, str) or not cmd_id:
        raise ProtocolError("command 'cmd_id' must be a non-empty string")
    exp = obj.get("exp")
    if not isinstance(exp, str) or not _TS_RE.match(exp):
        raise ProtocolError(f"command 'exp' not ISO-8601 with offset: {exp!r}")
    if "args" in obj and not isinstance(obj["args"], dict):
        raise ProtocolError("command 'args' must be an object when present")
    return obj


def validate_media(obj: Any) -> dict[str, Any]:
    """Return ``obj`` if it is a well-formed media frame, else raise
    :class:`ProtocolError`. Frame bytes are carried opaquely in ``b64``;
    this checks shape only, never that the bytes decode to an image."""
    if not isinstance(obj, dict):
        raise ProtocolError(f"media must be an object, got {type(obj).__name__}")
    allowed = {"fmt", "w", "h", "seq", "b64"}
    unknown = set(obj) - allowed
    if unknown:
        raise ProtocolError(f"media: unknown field(s): {', '.join(sorted(unknown))}")
    fmt = obj.get("fmt")
    if fmt not in MEDIA_FORMATS:
        raise ProtocolError(f"media 'fmt' not a known format: {fmt!r}")
    for field in ("w", "h", "seq"):
        if not _pos_int(obj.get(field)):
            raise ProtocolError(f"media {field!r} must be an integer >= 1")
    b64 = obj.get("b64")
    if not isinstance(b64, str) or not b64:
        raise ProtocolError("media 'b64' must be a non-empty string")
    return obj


# ---------- JSON Schema (draft 2020-12) generation ----------


def build_command_schema() -> dict[str, Any]:
    """The vendored ``command.schema.json`` — a strict object schema for a
    camera command plaintext (the sealed payload, not the envelope)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://diting.dev/companion-protocol/v3/command.schema.json",
        "title": "diting companion-protocol camera command (sealed payload)",
        "description": (
            "A phone->desktop control message driving a remote camera "
            "session. Sealed under the channel key; the relay never sees it."
        ),
        "type": "object",
        "required": ["cmd", "cmd_id", "exp"],
        "additionalProperties": False,
        "properties": {
            "cmd": {"type": "string", "enum": sorted(CAMERA_COMMANDS)},
            "cmd_id": {"type": "string", "minLength": 1},
            "exp": {"type": "string", "pattern": TS_PATTERN},
            "args": {"type": "object"},
        },
    }


def build_media_schema() -> dict[str, Any]:
    """The vendored ``media.schema.json`` — a strict object schema for one
    sealed still frame plaintext."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://diting.dev/companion-protocol/v3/media.schema.json",
        "title": "diting companion-protocol camera frame (sealed payload)",
        "description": (
            "One desktop->phone still frame. Sealed under the channel key; "
            "travels the ephemeral media route only, never the event log."
        ),
        "type": "object",
        "required": ["fmt", "w", "h", "seq", "b64"],
        "additionalProperties": False,
        "properties": {
            "fmt": {"type": "string", "enum": sorted(MEDIA_FORMATS)},
            "w": {"type": "integer", "minimum": 1},
            "h": {"type": "integer", "minimum": 1},
            "seq": {"type": "integer", "minimum": 1},
            "b64": {"type": "string", "minLength": 1},
        },
    }
