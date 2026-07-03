## Why

When the paired phone shows a network/RF anomaly (a link drop, a roam storm, an
unexpected new-device cluster), the operator often wants to *look* — is someone
at the desk, did the room change, is the Mac where it should be? Today the
companion channel only carries small JSON events desktop→phone; there is no way
to see the Mac's surroundings. This change adds an operator-initiated, low-rate
camera **snapshot** capability: from the phone, trigger the MacBook camera and
pull back a short burst of still frames, end-to-end encrypted over the existing
relay.

It is deliberately a low-rate snapshot feed, not a real-time surveillance
stream: 1 frame every 1–2 s, off by default, and every session is itself an
audited event. The session runs as long as the operator keeps the viewer open
(no fixed cap) but is held alive by a phone heartbeat, so a killed or
disconnected phone fails safe — the Mac stops on its own. The desktop is honest
about its limits (fixed lens, hardware capture LED that cannot and should not be
suppressed).

## What Changes

- **Protocol v3.** Two new *sealed message classes* that ride the existing
  envelope but are NOT events (they never enter the event vocabulary, the
  `EventLogger`, or the durable report):
  - `command` (phone→desktop): a sealed control message with a `cmd_id` and an
    `exp` expiry, carrying `camera.start` / `camera.keepalive` / `camera.stop`.
  - `media` (desktop→phone): a sealed still frame (`fmt`, `w`, `h`, `seq`,
    base64 JPEG), short-lived.
- **Reverse + media relay routes.** New relay tables + routes for a phone→desktop
  command queue (desktop polls) and a desktop→phone media queue (phone pulls,
  **pull-then-delete**, short TTL, low cap) — kept off the 7-day events table.
  Also bump the relay's stale `SUPPORTED_VERSIONS` (currently `{1}`, already
  behind the desktop's `{1,2}`) to accept v3.
- **Desktop inbound command loop.** The capture daemon gains a command-poll
  coroutine (peer of the existing flush loop) that, when the camera capability
  is enabled, drives a snapshot session: invoke the helper per frame, seal, push
  to the media route, and end the session on an explicit `stop` or when the
  phone's keepalive heartbeat lapses (fail-safe auto-stop).
- **`camsnap` in the macOS helper.** A new subcommand on the existing
  `diting-tianer.app` (inherits its TCC cdhash / one grant flow) that captures a
  single JPEG from the default camera and prints it schema-versioned to stdout;
  add `NSCameraUsageDescription` to the bundle Info.plist and a Camera step to
  the install-time permission flow.
- **Opt-in + audit.** Off by default; `diting companion camera on/off` gates it,
  and `on` performs a **foreground** first-run test capture so the TCC prompt is
  surfaced where it can actually appear. Every session start/stop emits a
  `camera_session` audit event that syncs to the phone like any other event.

## Capabilities

### New Capabilities
- `remote-camera`: the operator-initiated snapshot feature contract — session
  lifecycle (start / keepalive-heartbeat / explicit-stop / liveness-timeout
  auto-stop), snapshot cadence, the opt-in gate and first-run grant, the audit
  event, the security/threat model, and the honest capability limits (no
  pan/tilt, hardware LED not suppressible, relay sees only ciphertext).

### Modified Capabilities
- `companion-protocol`: adds v3 and the `command` + `media` sealed message
  classes (schemas, deterministic fixtures, manifest, version set) as a message
  family parallel to events; documents that direction is route-enforced, not
  auth-enforced.
- `companion-bridge`: adds the desktop inbound command-poll loop and media
  forwarding to the sink/daemon behaviour, gated by the camera opt-in.
- `macos-helper`: adds the `camsnap` subcommand, `NSCameraUsageDescription`, and
  the camera grant step; notes the cdhash change forces a one-time re-grant.

## Impact

- **Protocol** (`src/diting/companion/protocol/`): `version.py`,
  `_schema_spec.py` (or a parallel non-event schema path in `_generate.py`),
  `_generate.py`, regenerated `schema/` + `fixtures/` + `manifest.json`;
  `tests/test_companion_protocol.py` locks new hashes.
- **Relay** (`relay/`): `migrations/0003_command_media.sql`, `src/index.js`
  routes + `SUPPORTED_VERSIONS`, `test/relay.test.js`.
- **Daemon** (`src/diting/companion/runtime.py`, `sink.py`, `relay_client.py`;
  `src/diting/capture.py`): command-poll loop, media POST path, camera gate;
  `src/diting/cli.py`: `companion camera on/off`.
- **Helper** (`helper/`): `Sources/diting-tianer/main.swift` `camsnap` role,
  `Info.plist` camera key, `build.sh` unchanged, cdhash re-grant.
- **Downstream:** the phone side is a **separate change** in
  `chenchaoyi/diting-mobile` (`add-remote-camera` under `add-companion-sync`),
  which re-vendors the v3 `protocol/` and builds the session UI. This change
  ships the wire contract + desktop half; it does not touch the Flutter app.
- **Non-goals (v1):** live/real-time video (WebRTC/MJPEG), LAN direct fast-path,
  cloud archival of frames, microphone/audio, pan-tilt, multi-camera selection.
