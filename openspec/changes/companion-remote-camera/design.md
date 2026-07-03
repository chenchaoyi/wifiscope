# Design: companion-remote-camera

## Context

The companion channel today is a one-way, end-to-end-encrypted, store-and-forward
pipe: the desktop seals JSON *events* under the channel key (`crypto.py`
`seal_event`, XSalsa20-Poly1305), enqueues them (`sink.py` `offer`), and a flush
loop POSTs sealed envelopes to a Cloudflare Worker + D1 relay
(`relay_client.py`, `relay/src/index.js`); the phone pulls by cursor on
foreground-resume. The relay is *blind* — it stores opaque ciphertext with a
7-day TTL and cannot read payloads. There is no phone→desktop path of any kind,
and no binary/media transport.

Camera capture on macOS is gated by TCC, and a launchd/Terminal-run Python
daemon can never hold a camera grant — the same reason the project already
routes Location/Bluetooth through the `diting-tianer.app` helper bundle, whose
cdhash owns the grants (`macos-helper` spec). So the camera path must go through
that bundle, and the initial grant must be obtained in a **foreground** context
(a headless launchd process cannot reliably surface a TCC prompt).

## Goals / Non-Goals

**Goals**
- Operator-initiated still-frame session from the phone: ~1 frame / 1–2 s, no
  fixed frame/time cap — the session runs until the operator closes it on the
  phone, with a liveness heartbeat so a killed/backgrounded/disconnected phone
  fails safe (the Mac auto-stops rather than capturing forever).
- End-to-end encrypted: frames sealed under the channel key; the relay stores
  only ciphertext, briefly.
- Off by default; enabled by an explicit desktop opt-in with a foreground
  first-run grant; every session is an audited event synced to the phone.
- Reuse the existing crypto, envelope, pairing, gating, and helper-bundle
  machinery; keep the relay blind.

**Non-Goals**
- No live/real-time video (WebRTC, RTP, MJPEG stream). Snapshot burst only.
- No LAN direct fast-path in v1 (the main scenario is cross-network; add later).
- No cloud archival — frames are pull-then-delete with a short TTL.
- No audio/microphone, no pan/tilt, no multi-camera picker.
- No suppression of the hardware capture LED (impossible and undesirable).

## Decisions

### 1. `command` / `media` are a message family parallel to events, not event types

They ride the **existing envelope unchanged** (`ct` is an opaque, unbounded
base64 string — a sealed JPEG fits structurally) and use the **same
`seal`/`open` crypto** under the channel key. But they are NOT added to
`EVENT_SPEC` / `EventLogger` / the durable report, because:
- A 200 KB frame must never land in the 7-day events table or the report file.
- `command`/`media` are control/ephemeral, not observed facts; putting them in
  the event vocabulary would pollute the timeline and the `insight`/`threat`
  taxonomy.

Concretely: add plaintext schemas `command.schema.json` and `media.schema.json`
as **hand-built dicts in `_generate.py`** (exactly how `envelope`/`pairing`/
`apns-trigger` schemas are already hand-built there, i.e. *not* derived from
`_schema_spec.EVENT_SPEC`), with deterministic sealed golden fixtures
(mirroring `sealed-envelope.json`'s fixed-nonce seal) and manifest entries. The
seal/open helpers get thin `seal_command`/`seal_media` wrappers that stamp
`envelope_version_for` at v3. This keeps the event fixture generator
(`_generate.py:_emit_event_lines`, which runs the real `EventLogger`) untouched
— we do not need `EventLogger.emit_command/emit_media`.

**Trade-off:** two payload-schema families instead of one. Accepted — it keeps
media out of the event log, which is the whole point.

### 2. Protocol v3 and the relay version lag

`version.py`: `PROTOCOL_VERSION = 3`, `SUPPORTED_VERSIONS = {1,2,3}`. The relay
`SUPPORTED_VERSIONS` is currently `{1}` (`relay/src/index.js:12`) — already
behind the desktop's `{1,2}`, a latent interop bug for v2 `insight` envelopes.
Bump it to `{1,2,3}` in the same change and add a regression test so the two
sides can't silently drift again.

### 3. Reverse command queue + media queue as separate D1 tables, pull-then-delete

New migration `relay/migrations/0003_command_media.sql` adds two tables modelled
on `envelopes` but with **short TTL and delete-on-read**, kept off the events
table:
- `commands(channel, seq, ts, body, expiry, PK(channel,seq))` — phone `POST`s a
  sealed command; desktop `GET`s since a cursor and the row is deleted once
  delivered (or on `exp`).
- `media(channel, seq, ts, body, expiry, PK(channel,seq))` — desktop `POST`s
  sealed frames; phone `GET`s and rows are deleted on read.

Routes in `index.js`: `POST/GET /v1/channel/{id}/command`,
`POST/GET /v1/channel/{id}/media`, with their own low `MAX_MEDIA_PULL` (e.g. 8
frames/response) and short TTL (commands ~120 s, media ~600 s). Reuse
`authorizeExisting`/`validateEnvelope`. **Because both peers share one channel
bearer token, the relay cannot cryptographically tell phone from desktop** —
direction is enforced only by route convention. This is acceptable for a blind
relay whose worst case is a self-DoS of one's own channel; documented in the
threat model. (A future per-peer sub-key could harden it; out of scope.)

### 4. Desktop drives snapshots from a command-poll coroutine; session held open by phone heartbeat

Add `command_poll_loop(sink)` to `runtime.py` as a peer of `flush_loop`, spawned
in `capture.py:_spawn_consumers` and tracked for bounded teardown. It polls the
command route (~5 s idle) via `asyncio.to_thread` (blocking urllib). The command
message carries a `cmd` of `camera.start | camera.keepalive | camera.stop`, each
with a `cmd_id` and an `exp` expiry.

**Session lifecycle (no fixed cap — heartbeat-driven):**
- A valid, unexpired `camera.start` opens a session: while polling continues,
  capture one frame every `interval` via the helper `camsnap` subcommand
  (`subprocess.run([...,"camsnap"])` → base64 JPEG on stdout, the one-shot `scan`
  pattern), seal each as a `media` message, POST to the media route.
- The phone, while the viewer is open, sends `camera.keepalive` every
  `keepalive_interval` (~10 s). The desktop tracks the last-seen liveness time.
- The session ends when **either** an explicit `camera.stop` arrives (clean
  close — the normal path when the operator closes the viewer) **or** no
  keepalive/stop is seen within `liveness_timeout` (default ~30 s ≈ 3 missed
  beats — the fail-safe when the phone is killed, backgrounded, or loses
  network). Either way the session emits its `camera_session` stop audit event.

There is deliberately no maximum frame count or wall-clock cap: an operator
watching a suspected intrusion should be able to keep the feed open
indefinitely. Safety comes from the heartbeat (a vanished phone can't hold the
camera open) plus the honest hardware LED and the always-recorded audit event,
not from an arbitrary timeout. Duplicate/expired `cmd_id`s are ignored (replay
defence). The whole loop is inert unless the camera capability is enabled
(Decision 6) — an unpaired or camera-off daemon never even opens the command
route.

**Per-frame `subprocess.run`** (not a long-lived streaming helper) for v1:
simpler, no session state in Swift, each frame is independent. Cost is
process-spawn latency (~tens of ms) per frame — negligible at 1 fps. If a higher
rate is ever wanted, switch to the `ble-scan` Popen-JSONL streaming pattern.

### 5. `camsnap` as a subcommand of the existing helper bundle

Add a `camsnap` role to `helper/Sources/diting-tianer/main.swift`: configure an
`AVCaptureSession` with the default video device + `AVCapturephotoOutput` (or a
single `AVCapturePhotoSettings` JPEG grab), capture one frame, emit
`{"schema":1,"fmt":"jpeg","w":…,"h":…,"b64":…}` to stdout; TCC-denied → exit
code 3 (the helper's established convention). Add `NSCameraUsageDescription` to
`helper/Info.plist`. Reusing the existing bundle (vs a new `.app`) means one TCC
subject and one grant flow — but adding the Info.plist key **changes the
bundle's cdhash, so all existing helper grants (Location/Bluetooth) must be
re-granted once**; call this out in release notes and the install flow.

Frame parameters: default 1280×720 JPEG at quality ~0.6 → ~100–200 KB/frame
sealed. Configurable down for slow links.

### 6. Opt-in, foreground first-run grant, and audit

- **Gate:** a new persisted flag (in the companion state / prefs) read by the
  daemon; the feature is fully inert when off — no command polling, no route
  contact. Reuse the existing pairing gate (`runtime._state_path_if_paired`) so
  it is also inert when unpaired.
- **Enable:** `diting companion camera on` runs a **foreground** test capture so
  the AVFoundation TCC prompt appears where a user can approve it (a background
  launchd daemon can't surface it). `on` fails loudly if the grant is denied.
  `off` revokes the flag; the daemon stops polling on its next tick.
- **Audit:** session start and stop each emit a `camera_session` event (an
  ordinary sealed event, e.g. `{type:"camera_session", phase:"start|stop",
  frames:N, reason:…}`) that syncs to the phone and lands in the durable report
  — so the act of remote-viewing is itself a first-class, honest record on both
  ends. (This is the ONE place camera activity touches the event log — the
  metadata, never the frames.)

### 7. No desktop-local notification (deliberate)

Per the operator's call, `on`-session does not raise a macOS local notification
(the anti-theft scenario shouldn't tip off whoever holds the Mac). The hardware
capture LED still lights — a platform fact we state plainly in docs rather than
pretend around. The audit event is the honest record; it just isn't a desktop
popup.

## Risks / Trade-offs

- **cdhash re-grant churn.** Adding the camera Info.plist key invalidates
  existing Location/Bluetooth grants once. Mitigation: bundle it with a clear
  release note + the extended install flow re-requesting all grants in sequence.
- **Blind relay can't enforce direction.** Shared bearer token → the relay
  trusts route convention. Worst case is a channel owner DoS'ing their own
  channel; no cross-channel exposure (TOFU binding unchanged). Documented.
- **Not E2E to the frame-viewer's storage, only in transit + at relay.** Frames
  are sealed under the channel key end-to-end; the relay sees ciphertext. On the
  phone the decrypted frame lives in memory for the session only (no disk cache
  in v1). Stated as a limit.
- **TCC prompt can't be forced from the daemon.** Hence the foreground `camera
  on` grant step; a user who never runs it gets an honest "camera not authorised
  on the Mac" state on the phone rather than a silent hang.
- **Frame size vs relay caps.** Reusing the events queue/table would be wrong
  (Decision 3); the separate low-cap, short-TTL, delete-on-read media table
  bounds relay storage to a handful of in-flight frames per channel.
- **Unbounded session → battery / thermal / privacy.** With no cap, a session
  can run for hours. The heartbeat fail-safe (Decision 4) guarantees it cannot
  outlive the phone's attention — a killed/backgrounded/disconnected phone stops
  it within `liveness_timeout`. Remaining honest limits (drains the Mac battery,
  warms the machine, lights the hardware capture LED the whole time) are stated
  plainly on the phone's session UI rather than engineered around. The phone
  SHOULD stop keepalives when its own viewer is backgrounded, so the session
  tracks operator attention, not just app liveness.

## Migration

Protocol v3 is additive and back-compatible: v1/v2 peers keep working
(`SUPPORTED_VERSIONS` still includes them). A phone that hasn't re-vendored the
v3 `protocol/` simply never sends `camera.start` and ignores media — the feature
is invisible to it. The only forced action is the one-time helper re-grant from
the cdhash change, which affects existing Location/Bluetooth users regardless of
whether they use the camera.
