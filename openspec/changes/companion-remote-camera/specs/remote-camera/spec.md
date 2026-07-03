## ADDED Requirements

### Requirement: Remote camera capture SHALL be operator-initiated from the paired phone
The desktop SHALL NOT capture the camera on its own schedule. A capture session
SHALL begin only when the paired phone sends a sealed `camera.start` command over
the companion channel, and the desktop SHALL treat capture as a foreground
operator action, not background telemetry.

#### Scenario: No phone command, no capture
- **WHEN** the desktop daemon is running, paired, and camera-enabled, but no `camera.start` command has been received
- **THEN** the camera is never opened and no frames are captured

#### Scenario: Phone starts a session
- **WHEN** the phone sends a valid, unexpired `camera.start` command and the camera capability is enabled
- **THEN** the desktop opens a session and begins emitting sealed still frames at the configured cadence

### Requirement: Camera capture SHALL be off by default and enabled only by an explicit foreground grant
The camera capability SHALL be disabled by default. Enabling it SHALL require an
explicit desktop action (`diting companion camera on`) that performs a foreground
test capture so the macOS camera TCC prompt can be surfaced and resolved. If the
grant is denied, enabling SHALL fail loudly and the capability SHALL remain off.
A camera-off or unpaired daemon SHALL be fully inert — it SHALL NOT poll the
command route.

#### Scenario: Enabling surfaces the grant
- **WHEN** the operator runs `diting companion camera on` for the first time
- **THEN** a foreground test capture runs, the macOS camera prompt appears, and the capability is marked enabled only if the grant resolves to authorized

#### Scenario: Denied grant leaves it off
- **WHEN** the operator denies the camera prompt during enable
- **THEN** the command reports the denial, the capability stays disabled, and the daemon does not poll for camera commands

#### Scenario: Disabled daemon is inert
- **WHEN** the camera capability is off (or the desktop is unpaired)
- **THEN** the daemon performs no command polling and opens no camera route

### Requirement: A session SHALL run until the operator closes it or the phone's liveness heartbeat lapses
A session SHALL have no fixed frame count or wall-clock cap. It SHALL end when
either an explicit `camera.stop` command arrives, or no `camera.keepalive` (or
`stop`) command is seen within a bounded liveness timeout. The phone SHALL send
`camera.keepalive` at a shorter interval than the timeout while its viewer is in
the foreground, and SHOULD stop sending keepalives when the viewer is
backgrounded so the session tracks operator attention.

#### Scenario: Operator closes the viewer
- **WHEN** the operator closes the camera viewer on the phone and the phone sends `camera.stop`
- **THEN** the desktop ends the session promptly and stops capturing

#### Scenario: Phone vanishes mid-session
- **WHEN** the phone is killed, backgrounded, or loses network during a session and its keepalives stop arriving
- **THEN** the desktop auto-stops the session within the liveness timeout rather than capturing indefinitely

### Requirement: Every session SHALL be recorded as an audit event on both ends
Session start and session stop SHALL each emit a `camera_session` event (an
ordinary sealed companion event carrying session metadata such as phase, frame
count, and stop reason) that syncs to the phone and persists in the durable
report. Frame payloads SHALL NEVER enter the event log or the durable report —
only the session metadata does.

#### Scenario: Start and stop are audited
- **WHEN** a session starts and later stops
- **THEN** a `camera_session` start event and a `camera_session` stop event are emitted, sync to the phone, and appear in the durable report

#### Scenario: Frames are not logged
- **WHEN** frames are captured during a session
- **THEN** no frame bytes appear in the event log or the durable report; frames travel only over the ephemeral media route

### Requirement: Frames SHALL be sealed end-to-end and the desktop SHALL state capture limits honestly
Each frame SHALL be sealed under the channel key so the relay stores only
ciphertext. The feature SHALL NOT suppress or misrepresent the Mac's hardware
capture indicator, SHALL NOT claim capabilities the fixed built-in camera lacks
(no pan/tilt/zoom, single camera), and the phone UI SHALL state the running-cost
limits (battery drain, thermals, the always-lit hardware LED) plainly rather than
imply a covert capture.

#### Scenario: Relay never sees plaintext frames
- **WHEN** a sealed frame transits the relay
- **THEN** the relay stores and forwards ciphertext only and cannot recover the image

#### Scenario: Honest limits, not covert capture
- **WHEN** a session is active
- **THEN** the Mac's hardware capture indicator is lit (not suppressed) and the phone UI states the running costs and the fixed-lens limits rather than presenting the feed as covert
