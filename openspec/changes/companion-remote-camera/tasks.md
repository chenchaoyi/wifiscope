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

- [x] 2.1 `relay/migrations/0003_command_media.sql`: `commands` and `media`
      tables (PK `(channel,seq)`, `expiry`), short TTL, modelled on `envelopes`
- [x] 2.2 `relay/src/index.js`: bump `SUPPORTED_VERSIONS` `{1}` → `{1,2,3}` (fixes
      the existing v2 lag); add routes `POST/GET /command` and `POST/GET /media`
      via generic `handleQueueStore`/`handleQueueDrain` (table allowlisted),
      delete-on-delivery, `MAX_MEDIA_PULL=8` / `MAX_COMMAND_PULL=64`, TTL
      commands 120 s / media 600 s; reuse `authorizeOrBind`/`authorizeExisting`/
      `validateEnvelope` (relay stays blind); unpair clears both queues
- [x] 2.3 `relay/test/relay.test.js`: command enqueue→drain (ordered),
      delete-on-delivery, TTL expiry, unsupported-version reject, auth;
      media push→pull-then-delete, `MAX_MEDIA_PULL` cap + remainder,
      expiry; unpair clears queues; v2/v3 accepted regression. 30 tests pass
- [ ] 2.4 Apply migration remotely (`npm run migrate:remote`) at release — deploy
      step, not code; run when the Worker is next deployed

## 3. macOS helper — `camsnap`

- [x] 3.1 `helper/Sources/diting-tianer/main.swift`: added a `camsnap` role —
      `AVCaptureVideoDataOutput` first-frame grab (disclaim hop like ble-scan),
      optional downscale, JPEG encode; emits
      `{"schema":1,"fmt":"jpeg","w":…,"h":…,"b64":…}`; auth-denied → exit 3,
      restricted → 5, timeout/no-device → 2; `--width/--height/--quality`
- [x] 3.2 `helper/Info.plist`: added `NSCameraUsageDescription` (cdhash change
      forces a one-time re-grant of existing Location/Bluetooth permissions)
- [~] 3.3 Install-sequence Camera step deferred — `camera on` does a foreground
      test capture that surfaces the prompt, so the install flow need not change
      for the feature to work; folding Camera into `HelperAppDelegate` is polish
- [x] 3.4 `swift build -c release` clean (camsnap compiles); `find_helper`
      resolution unchanged

## 4. Desktop daemon — command loop, session, camera gate

- [x] 4.1 `relay_client.py`: `poll_commands()` (GET `/command`) + `post_media()`
      (POST `/media`), mirroring `fetch_presence`/`_post`
- [x] 4.2 `companion/camera.py` `CameraSessionDriver` (pure sync `tick()`) +
      `runtime.py` `command_poll_loop` (peer of `flush_loop`, `asyncio.to_thread`)
      — drains commands, per-frame helper `camsnap` → `seal_media` → `post_media`,
      liveness-timeout auto-stop, replay/stale-`cmd_id` defence; `sink.py`
      `drain_commands`/`send_frame` keep the key encapsulated
- [x] 4.3 `capture.py`: spawn `command_poll_loop` in `_spawn_consumers` gated on
      `sink.camera_enabled`, tracked for bounded teardown
- [x] 4.4 Camera gate: `PairingState.camera_enabled` (state json, back-compat
      default False); loop inert when off or unpaired
- [x] 4.5 `cli.py`: `diting companion camera on|off|status` — `on` runs a
      foreground test capture, enables only on an authorized grant, else reports
      the denial and exits non-zero; `off` clears the flag; i18n EN+ZH
- [ ] 4.6 DEFERRED to a follow-up change: `camera_session` start/stop audit
      events. Adds a new `EVENT_SPEC` type → regenerates artifacts → forces a
      mobile re-vendor + events-map rendering, so it ships separately to keep
      this change's capture path focused. Frames still flow without it.

## 5. Docs, i18n, tests, gates

- [x] 5.1 `tests/TESTING.md` + `docs/zh/TESTING.md`: remote-camera test plan
      (session driver lifecycle, liveness timeout, replay/stale defence, sink
      command/media plane, relay routes, camsnap exit-code map, CLI gate)
- [x] 5.2 `i18n.py`: `companion camera` CLI strings in EN + ZH parity
      (`test_i18n.py` catalog-coverage guard passes)
- [x] 5.3 `README.md` + `docs/zh/README.md`: `companion camera on` opt-in line
- [x] 5.4 Gates: `uv run pytest` green, `tui_snapshot --mode regression` pass,
      `openspec validate --specs --strict` (31) + change `--strict` valid;
      `swift build -c release` clean for the helper
- [x] 5.5 Re-vendor note: the phone side shipped separately in
      `chenchaoyi/diting-mobile` (#64 transport, #65 viewer UI)
