# Tasks: companion-remote-camera

## 1. Protocol v3 — command + media message classes

- [x] 1.1 `src/diting/companion/protocol/version.py`: `PROTOCOL_VERSION = 3`,
      `SUPPORTED_VERSIONS = {1,2,3}`, `MESSAGE_MIN_VERSION = 3` (command/media
      stamp at v3)
- [x] 1.2 `protocol/messages.py`: `command`/`media` runtime validators
      (`validate_command`/`validate_media`) + JSON Schema builders
      (`build_command_schema`/`build_media_schema`) — one source, two artifacts,
      parallel to `events_schema` and NOT via `EVENT_SPEC`; `_generate.py` writes
      `schema/command.schema.json` + `schema/media.schema.json`
- [x] 1.3 `crypto.py`: refactor shared `_seal`/`_open`, add
      `seal_command`/`open_command` and `seal_media`/`open_media` (validate then
      seal / open then validate); media/command never routed through
      `EventLogger`
- [x] 1.4 Deterministic golden fixtures `fixtures/sealed-command.json` +
      `fixtures/sealed-media.json` (fixed key + fixed nonce, mirroring
      `sealed-envelope.json`); auto-hashed into `manifest.json` with
      `protocol_version: 3`
- [x] 1.5 Regenerated artifacts; `tests/test_companion_protocol.py` covers both
      message classes (validate accept/fail-closed, seal↔open round-trip, v3
      stamp, wrong-key auth failure, cross-class open fails closed, committed
      fixtures open, manifest carries the new artifacts); full suite 1604 passed

## 2. Relay — reverse command + media routes

- [ ] 2.1 `relay/migrations/0003_command_media.sql`: `commands` and `media`
      tables (PK `(channel,seq)`, `expiry`), short TTL, modelled on `envelopes`
- [ ] 2.2 `relay/src/index.js`: bump `SUPPORTED_VERSIONS` `{1}` → `{1,2,3}` (fixes
      the existing v2 lag); add routes `POST/GET /command` and `POST/GET /media`
      with a low `MAX_MEDIA_PULL`, short TTL, and delete-on-delivery (SELECT→
      DELETE / DELETE…RETURNING); reuse `authorizeExisting`/`validateEnvelope`
- [ ] 2.3 `relay/test/relay.test.js`: command enqueue→drain, media
      push→pull-then-delete, media cap, TTL expiry, and a regression test that
      the relay accepts every version the desktop can emit
- [ ] 2.4 Apply migration remotely (`npm run migrate:remote`) as part of release

## 3. macOS helper — `camsnap`

- [ ] 3.1 `helper/Sources/diting-tianer/main.swift`: add a `camsnap` role —
      `AVCaptureSession` + default video device + single JPEG grab; emit
      `{"schema":1,"fmt":"jpeg","w":…,"h":…,"b64":…}`; TCC-denied → exit 3;
      optional `--width/--height/--quality`
- [ ] 3.2 `helper/Info.plist`: add `NSCameraUsageDescription`; note the cdhash
      change forces a one-time re-grant of existing permissions
- [ ] 3.3 Extend the install permission sequence to Location → Bluetooth →
      Notifications → Camera (the `HelperAppDelegate` flow)
- [ ] 3.4 `make helper` builds clean; `find_helper` resolution unchanged

## 4. Desktop daemon — command loop, session, camera gate

- [ ] 4.1 `src/diting/companion/relay_client.py`: `poll_commands(since)` (GET
      command route) and `post_media(frame)` (POST media route), mirroring
      `fetch_presence`/`_post`
- [ ] 4.2 `src/diting/companion/runtime.py`: `command_poll_loop(sink)` coroutine
      (peer of `flush_loop`, `asyncio.to_thread` for blocking urllib) — drains
      commands, drives the session (per-frame `subprocess.run([helper,"camsnap"])`
      → seal media → post), refreshes liveness on keepalive, ends on stop or
      liveness timeout; ignores expired/replayed `cmd_id`
- [ ] 4.3 `src/diting/capture.py`: spawn `command_poll_loop` in
      `_spawn_consumers` only when the camera flag is enabled, track it for
      bounded teardown
- [ ] 4.4 Camera gate: persist a camera-enabled flag in companion state; the loop
      is inert when off or unpaired
- [ ] 4.5 `src/diting/cli.py`: `diting companion camera on|off` — `on` runs a
      foreground test capture, enables only if the grant is authorized, else
      reports denial; `off` clears the flag
- [ ] 4.6 Emit `camera_session` start/stop audit events (metadata only) through
      the normal sink; add `camera_session` to the event vocabulary (`EVENT_SPEC`)
      + its golden fixture

## 5. Docs, i18n, tests, gates

- [ ] 5.1 `tests/TESTING.md` (EN + ZH): add the remote-camera test plan BEFORE
      writing test code (protocol conformance, relay routes, session lifecycle,
      liveness timeout, opt-in gate, audit event)
- [ ] 5.2 `i18n.py`: any new user-facing strings (CLI `camera on/off` output,
      `camera_session` rendering) in EN + ZH parity
- [ ] 5.3 `README.md` + `docs/zh/README.md`: document the remote-camera opt-in,
      the grant flow, and the honest limits (LED, battery, no covert capture)
- [ ] 5.4 Gates: `uv run pytest`, `uv run python scripts/tui_snapshot.py --mode
      regression`, `openspec validate --specs --strict`, `openspec validate
      companion-remote-camera --strict`
- [ ] 5.5 Re-vendor note: the phone side (re-vendor v3 `protocol/`, build the
      session UI) is a SEPARATE change in `chenchaoyi/diting-mobile`; this change
      ships the wire contract + desktop half only
