-- diting companion relay — reverse command + ephemeral media queues (v3).
--
-- The durable `envelopes` table is desktop->phone event history with a
-- 7-day TTL. The remote-camera control/media plane is different: two
-- short-lived, delete-on-delivery queues that must never accumulate on
-- the blind relay.
--
--   commands  phone->desktop control (camera.start/keepalive/stop). The
--             desktop drains it on its poll; drained rows are deleted.
--   media     desktop->phone sealed still frames. The phone pulls them;
--             pulled rows are deleted (pull-then-delete).
--
-- Same blind shape as `envelopes` (ciphertext body + routing metadata,
-- the relay never reads `ct`), but a short expiry and delete-on-read so a
-- vanished peer can't leave a backlog. Both keep PRIMARY KEY (channel,
-- seq) so a retried POST is idempotent; the consumer dedupes on the
-- sealed cmd_id / frame seq regardless.

CREATE TABLE IF NOT EXISTS commands (
  channel TEXT NOT NULL,
  seq     INTEGER NOT NULL,              -- phone-assigned, monotonic per channel
  ts      TEXT NOT NULL,                 -- producer wall-clock (opaque to relay)
  body    TEXT NOT NULL,                 -- full envelope JSON (ciphertext inside)
  expiry  INTEGER NOT NULL,              -- unix seconds; row is dead past this
  PRIMARY KEY (channel, seq)
);

CREATE INDEX IF NOT EXISTS idx_cmd_channel_seq ON commands (channel, seq);
CREATE INDEX IF NOT EXISTS idx_cmd_expiry ON commands (expiry);

CREATE TABLE IF NOT EXISTS media (
  channel TEXT NOT NULL,
  seq     INTEGER NOT NULL,              -- desktop-assigned, monotonic per channel
  ts      TEXT NOT NULL,                 -- producer wall-clock (opaque to relay)
  body    TEXT NOT NULL,                 -- full envelope JSON (sealed frame inside)
  expiry  INTEGER NOT NULL,              -- unix seconds; row is dead past this
  PRIMARY KEY (channel, seq)
);

CREATE INDEX IF NOT EXISTS idx_media_channel_seq ON media (channel, seq);
CREATE INDEX IF NOT EXISTS idx_media_expiry ON media (expiry);
