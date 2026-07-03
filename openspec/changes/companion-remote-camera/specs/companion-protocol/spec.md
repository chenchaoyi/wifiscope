## MODIFIED Requirements

### Requirement: Protocol is versioned and back-compatible
The protocol SHALL carry an explicit integer major version in the pairing
payload and in every relay request path. The current version SHALL be `v3`, and
the supported set SHALL be `{1, 2, 3}`. A peer SHALL tolerate an envelope or
pairing payload stamped with a version it recognises, and SHALL refuse — with a
clear, non-crashing error — a version it does not recognise. A newer producer
SHALL NOT change the meaning of an existing field of an earlier version;
additive fields only, mirroring the macOS-helper schema rule. Both the desktop
and the relay SHALL share the same supported-version set; a divergence (e.g. the
relay accepting a narrower set than the desktop emits) SHALL be treated as a bug
and covered by a regression test.

#### Scenario: Consumer receives a known version
- **WHEN** a consumer pulls an envelope stamped `v1` and the consumer supports `v1`
- **THEN** it decrypts and processes the envelope normally

#### Scenario: Consumer receives an unknown future version
- **WHEN** a consumer supporting only `{1,2,3}` encounters a payload stamped `v4`
- **THEN** it abstains from processing that payload and surfaces a "newer protocol" notice without crashing or dropping its cursor

#### Scenario: Relay and desktop agree on the supported set
- **WHEN** the desktop emits an envelope at the current protocol version
- **THEN** the relay accepts it (the relay's supported-version set includes every version the desktop can emit), verified by a test that fails on drift

### Requirement: Relay exposes a blind store-and-forward HTTP API
The relay SHALL accept `POST /v1/channel/{id}` carrying a ciphertext envelope
and SHALL serve `GET /v1/channel/{id}?since={cursor}` returning that channel's
envelopes in ascending sequence order after the cursor. In addition to this
durable event path, the relay SHALL expose two ephemeral routes for the remote
control/media plane:
- a phone→desktop command queue: `POST /v1/channel/{id}/command` (enqueue a
  sealed command) and `GET /v1/channel/{id}/command?since={cursor}` (desktop
  drains it);
- a desktop→phone media queue: `POST /v1/channel/{id}/media` (push a sealed
  frame) and `GET /v1/channel/{id}/media` (phone pulls).
The command and media queues SHALL use a short TTL and delete-on-delivery
semantics (they are not durable history) and SHALL have their own low per-pull
cap, kept off the durable events store. Stored durable items SHALL expire after a
bounded TTL. All requests SHALL be authenticated per channel. The relay SHALL
store and return ciphertext + routing metadata only and SHALL be incapable of
reading event, command, or frame plaintext. Because both peers authenticate with
the same channel credential, the relay SHALL NOT be relied on to distinguish
which peer is which; message direction is a route convention, not an
authenticated property.

#### Scenario: Store then forward
- **WHEN** a producer POSTs an envelope and a consumer later GETs since an earlier cursor
- **THEN** the consumer receives that envelope in sequence order

#### Scenario: Expired items drop out
- **WHEN** an envelope older than the configured TTL is requested
- **THEN** the relay no longer returns it, and the consumer treats it as an unrecoverable gap rather than an error

#### Scenario: Unauthorized channel access is refused
- **WHEN** a request omits or presents wrong channel credentials
- **THEN** the relay returns an auth error and reveals no stored bytes

#### Scenario: Media is pull-then-delete and bounded
- **WHEN** the phone GETs the media queue and receives frames
- **THEN** those frames are removed from the queue after delivery, a single pull returns no more than the media cap, and unpulled frames expire under the short media TTL

### Requirement: Machine-readable contract artifacts are canonical here
This repository SHALL hold the authoritative JSON Schema for the envelope, for
each event type, and for each non-event sealed message class (the `command` and
`media` plaintext payloads), plus a set of golden fixture lines exercising every
event type, every message class, and edge cases (omitted `None`, empty `[]`, CJK
strings, a sealed media frame). Downstream consumers SHALL vendor these artifacts
and run a conformance test against them; the artifacts SHALL carry a
version/hash so a vendored copy that drifts is detectable, and `manifest.json`
SHALL record the current `protocol_version`.

#### Scenario: Conformance fixtures cover every type and message class
- **WHEN** the fixture set is validated
- **THEN** it contains at least one golden line per event type defined in `events` and one per non-event message class (`command`, `media`), and each validates against its JSON Schema

#### Scenario: Drift is detectable
- **WHEN** a consumer's vendored copy of the artifacts differs from the canonical version/hash
- **THEN** the consumer's drift check fails rather than silently running against a stale contract

## ADDED Requirements

### Requirement: Command and media are sealed message classes parallel to events
The protocol SHALL define `command` and `media` as sealed message classes that
ride the existing envelope and the same authenticated-encryption seal as events,
but are NOT part of the event vocabulary: they SHALL NOT be produced by the event
logger, SHALL NOT enter the durable report, and SHALL NOT appear in the events
timeline. A `command` plaintext SHALL carry a command name (`camera.start` /
`camera.keepalive` / `camera.stop`), a unique `cmd_id`, and an `exp` expiry so a
replayed or stale command is rejected. A `media` plaintext SHALL carry the frame
format, pixel dimensions, an in-session frame sequence, and the base64 image
bytes. Both SHALL be stamped at protocol version 3.

#### Scenario: Command carries replay defence
- **WHEN** a `camera.start` command is received whose `cmd_id` was already seen, or whose `exp` is in the past
- **THEN** the desktop ignores it rather than opening a duplicate or stale session

#### Scenario: Media never touches the event log
- **WHEN** a `media` message is sealed, sent, and opened
- **THEN** it flows only over the media route and is never written to the event log, the durable report, or the events timeline

#### Scenario: Message classes seal like events
- **WHEN** a `command` or `media` plaintext is sealed under the channel key
- **THEN** it uses the same envelope shape and authenticated-encryption seal as an event, and a wrong key or tampered ciphertext fails closed
