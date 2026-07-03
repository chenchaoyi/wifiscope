## ADDED Requirements

### Requirement: The desktop SHALL poll for and execute sealed camera commands when camera capture is enabled
The capture daemon SHALL run an inbound command-poll loop — when the camera
capability is enabled and the desktop is paired — as a peer of the outbound flush
loop, tracked for bounded teardown alongside the other consumer tasks. The loop
SHALL drain the relay command route, open the channel key to read each sealed
command, and drive a snapshot session: on `camera.start` it SHALL capture one
frame per configured interval via the macOS helper, seal each frame as a `media`
message, and POST it to the media route; it SHALL refresh session liveness on
each `camera.keepalive`; and it SHALL end the session on `camera.stop` or when
the liveness timeout lapses. Expired or replayed `cmd_id`s SHALL be ignored. When
the capability is off or the desktop is unpaired, the loop SHALL NOT run and the
command route SHALL NOT be contacted.

#### Scenario: Command loop runs only when enabled and paired
- **WHEN** the daemon starts with the camera capability enabled and a valid pairing
- **THEN** it spawns the command-poll loop alongside the flush loop and tears it down cleanly on shutdown

#### Scenario: Start drives a sealed frame session
- **WHEN** a valid `camera.start` is drained from the command route
- **THEN** the desktop captures frames at the configured cadence, seals each as a `media` message, and POSTs them to the media route until the session ends

#### Scenario: Liveness timeout stops an abandoned session
- **WHEN** a session is open and no `camera.keepalive` or `camera.stop` arrives within the liveness timeout
- **THEN** the desktop stops capturing and emits the session-stop audit event

### Requirement: Camera capture SHALL be a distinct opt-in from event forwarding
The camera capability SHALL be gated by its own persisted flag, separate from the
event-forwarding opt-in, and off by default. It SHALL be toggled by a
`diting companion camera on|off` surface, where `on` performs a foreground test
capture to resolve the macOS camera grant before marking the capability enabled,
and `off` disables it so the command-poll loop stops on its next tick. The camera
gate SHALL compose with — not replace — the existing pairing and
forwarding-opt-in gates, so enabling the camera on an unpaired or
forwarding-disabled daemon SHALL still leave the loop inert until pairing exists.

#### Scenario: Camera flag is independent of forwarding
- **WHEN** event forwarding is enabled but the camera flag is off
- **THEN** events sync normally and no camera command polling occurs

#### Scenario: Enable resolves the grant in the foreground
- **WHEN** the operator runs `diting companion camera on`
- **THEN** a foreground test capture runs and the capability is enabled only if the camera grant resolves to authorized, otherwise it reports the denial and stays off

#### Scenario: Disable halts polling
- **WHEN** the operator runs `diting companion camera off` while the daemon is running
- **THEN** the command-poll loop stops contacting the command route on its next tick
