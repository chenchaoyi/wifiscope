"""Desktop remote-camera capture: session driver (streaming), sink
command/media plane, relay routes, helper camstream/camsnap wrappers, state
flag, and the `companion camera` CLI."""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timedelta, timezone

import pytest

from diting import _helper
from diting.companion.camera import CameraSessionDriver
from diting.companion.crypto import open_media, seal_command
from diting.companion.push_policy import PushPolicy
from diting.companion.relay_client import RelayClient
from diting.companion.sink import CompanionSink
from diting.companion.state import PairingState, load_state

_TZ = timezone(timedelta(hours=8))


# ---------- session driver (streaming) ----------

class _FakeSink:
    """Serves queued command batches (one list per tick) and records frames."""

    def __init__(self, batches=None):
        self._batches = list(batches or [])
        self.frames: list[dict] = []

    def queue(self, cmds):
        self._batches.append(cmds)

    def drain_commands(self):
        return self._batches.pop(0) if self._batches else []

    def send_frame(self, frame):
        self.frames.append(frame)
        return 200


class _FakeStream:
    """A finite stream: yields the given frames then ends (like the helper
    exiting). `close` marks it closed and unblocks a blocking variant."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def frames(self):
        for f in self._frames:
            yield f

    def close(self):
        self.closed = True


class _BlockingStream:
    """Blocks in `frames` until `close` — models a live camera that keeps
    streaming until the session stops it."""

    def __init__(self):
        self.closed = False
        self._stop = threading.Event()

    def frames(self):
        self._stop.wait(timeout=2)
        return
        yield  # unreachable — marks this a generator

    def close(self):
        self.closed = True
        self._stop.set()


def _cmd(name, cid, *, exp_at):
    return {"cmd": name, "cmd_id": cid, "exp": exp_at.isoformat()}


class _Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


_A_FRAME = {"fmt": "jpeg", "w": 4, "h": 3, "b64": "Zm9v"}


def _driver(sink, *, clock, open_stream=None, **kw):
    if open_stream is None:
        open_stream = lambda: _FakeStream([_A_FRAME])  # noqa: E731
    return CameraSessionDriver(sink, open_stream, now=clock, **kw)


def _wait_pump(driver, timeout=2.0):
    pump = driver._pump
    if pump is not None:
        pump.join(timeout=timeout)


def test_start_opens_stream_and_forwards_frames_in_sequence():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    stream = _FakeStream([dict(_A_FRAME), dict(_A_FRAME), dict(_A_FRAME)])
    sink = _FakeSink([[_cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))]])
    d = _driver(sink, clock=clock, open_stream=lambda: stream)
    d.tick()
    assert d.active
    _wait_pump(d)  # the pump forwards all frames then ends (finite stream)
    assert [f["seq"] for f in sink.frames] == [1, 2, 3]  # in-session frame seq


def test_stream_open_failure_does_not_start_a_session():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    sink = _FakeSink([[_cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))]])
    d = _driver(sink, clock=clock, open_stream=lambda: None)  # helper missing/denied
    d.tick()
    assert not d.active
    assert sink.frames == []


def test_liveness_timeout_auto_stops_and_closes_the_stream():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    stream = _BlockingStream()
    sink = _FakeSink([[_cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))]])
    d = _driver(sink, clock=clock, open_stream=lambda: stream, liveness_timeout=30)
    d.tick()
    assert d.active
    clock.advance(31)  # no keepalive within the window
    d.tick()
    assert not d.active
    assert stream.closed  # the camera stream was torn down


def test_keepalive_holds_the_session_open():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    sink = _FakeSink([[_cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))]])
    d = _driver(sink, clock=clock, open_stream=lambda: _BlockingStream(), liveness_timeout=30)
    d.tick()
    clock.advance(20)
    sink.queue([_cmd("camera.keepalive", "k1", exp_at=clock.now + timedelta(seconds=60))])
    d.tick()
    clock.advance(20)  # 40s since start, only 20s since keepalive
    d.tick()
    assert d.active


def test_stop_command_ends_and_closes_the_stream():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    stream = _BlockingStream()
    sink = _FakeSink([[_cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))]])
    d = _driver(sink, clock=clock, open_stream=lambda: stream)
    d.tick()
    sink.queue([_cmd("camera.stop", "x1", exp_at=clock.now + timedelta(seconds=60))])
    d.tick()
    assert not d.active
    assert stream.closed


def test_replayed_and_stale_commands_are_ignored():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    dup = _cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))
    stale = _cmd("camera.start", "s2", exp_at=clock.now - timedelta(seconds=1))
    opens = []
    sink = _FakeSink([[dup]])
    d = _driver(sink, clock=clock, open_stream=lambda: opens.append(1) or _BlockingStream())
    d.tick()  # first start opens the stream
    assert d.active and len(opens) == 1
    sink.queue([dup, stale])  # replayed id + expired command
    d.tick()
    assert len(opens) == 1  # no second stream opened


def test_only_one_stream_per_session_on_repeated_start():
    clock = _Clock(datetime(2026, 5, 20, 12, 0, 0, tzinfo=_TZ))
    opens = []
    sink = _FakeSink([[_cmd("camera.start", "s1", exp_at=clock.now + timedelta(seconds=60))]])
    d = _driver(sink, clock=clock, open_stream=lambda: opens.append(1) or _BlockingStream())
    d.tick()
    # A *fresh* start (new id) while already active just refreshes liveness.
    sink.queue([_cmd("camera.start", "s2", exp_at=clock.now + timedelta(seconds=60))])
    d.tick()
    assert len(opens) == 1


# ---------- sink command/media plane ----------

class _FakeTransport:
    def __init__(self, status=200):
        self.calls = []
        self._status = status

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "body": json.loads(body)})
        return self._status


def _sink(tmp_path, get_transport=None, transport=None):
    path = tmp_path / "companion.json"
    st = PairingState.generate("https://r.example")
    st.save(path)
    client = RelayClient(
        st.relay_url,
        st.channel,
        st.relay_token(),
        transport=transport or _FakeTransport(200),
        get_transport=get_transport or (lambda url, headers: None),
    )
    return st, CompanionSink(st, client, PushPolicy(), state_path=path), path


def test_sink_drain_commands_opens_sealed(tmp_path):
    path = tmp_path / "companion.json"
    st = PairingState.generate("https://r.example")
    st.save(path)
    env = seal_command(
        st.key_bytes(),
        channel=st.channel,
        seq=1,
        ts="2026-05-20T12:00:00+08:00",
        command={"cmd": "camera.start", "cmd_id": "x", "exp": "2026-05-20T12:00:31+08:00"},
    )
    body = json.dumps({"envelopes": [env]}).encode()
    client = RelayClient(
        st.relay_url, st.channel, st.relay_token(),
        get_transport=lambda url, headers: body,
    )
    sink = CompanionSink(st, client, PushPolicy(), state_path=path)
    cmds = sink.drain_commands()
    assert cmds[0]["cmd"] == "camera.start"


def test_sink_send_frame_uses_its_own_seq_not_the_event_cursor(tmp_path):
    st, sink, path = _sink(tmp_path)
    tx = sink.client._transport  # the _FakeTransport
    frame = {"fmt": "jpeg", "w": 4, "h": 3, "seq": 1, "b64": "Zm9v"}
    assert sink.send_frame(frame) == 200
    assert sink.send_frame(frame) == 200
    assert tx.calls[0]["url"].endswith("/media")
    env0, env1 = tx.calls[0]["body"], tx.calls[1]["body"]
    assert env0["v"] == 3
    assert env0["seq"] == 1 and env1["seq"] == 2  # media's own monotonic seq
    assert open_media(st.key_bytes(), env0) == frame
    # Media must NOT consume the persisted event cursor (else the phone's
    # event pull would see false gaps).
    assert load_state(path).last_seq == 0


def test_sink_camera_enabled_reflects_state(tmp_path):
    _st, sink, _path = _sink(tmp_path)
    assert sink.camera_enabled is False


# ---------- relay routes ----------

def test_poll_commands_parses_and_hits_command_route():
    seen = {}

    def get_tx(url, headers):
        seen["url"] = url
        return b'{"envelopes":[{"v":3,"ch":"c","seq":1,"ts":"t","n":"n","ct":"ct"}]}'

    c = RelayClient("https://r.example/", "chan-1", "tok", get_transport=get_tx)
    envs = c.poll_commands()
    assert len(envs) == 1 and envs[0]["seq"] == 1
    assert seen["url"].endswith("/v1/channel/chan-1/command")


@pytest.mark.parametrize("get_tx", [
    lambda url, headers: None,       # transport failure
    lambda url, headers: b"nope",    # unparseable
    lambda url, headers: b"{}",      # no envelopes key
])
def test_poll_commands_degrades_to_empty(get_tx):
    c = RelayClient("https://r.example/", "chan-1", "tok", get_transport=get_tx)
    assert c.poll_commands() == []


def test_post_media_hits_media_route():
    tx = _FakeTransport(200)
    c = RelayClient("https://r.example/", "chan-1", "tok", transport=tx)
    assert c.post_media({"v": 3, "ch": "chan-1", "seq": 1, "ts": "t", "n": "n", "ct": "ct"}) == 200
    assert tx.calls[0]["url"].endswith("/v1/channel/chan-1/media")


# ---------- state flag ----------

def test_camera_enabled_round_trips_and_defaults_false(tmp_path):
    path = tmp_path / "companion.json"
    st = PairingState.generate("https://r.example")
    assert st.camera_enabled is False
    st.camera_enabled = True
    st.save(path)
    assert load_state(path).camera_enabled is True


def test_camera_enabled_back_compat_missing_key(tmp_path):
    path = tmp_path / "companion.json"
    st = PairingState.generate("https://r.example")
    data = st.to_dict()
    del data["camera_enabled"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_state(path).camera_enabled is False


# ---------- helper camstream + camsnap wrappers ----------

class _FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def test_camstream_frames_parses_jsonl_and_skips_junk():
    lines = [
        '{"schema":1,"fmt":"jpeg","w":4,"h":3,"b64":"Zm9v"}\n',
        "not json\n",
        '{"schema":1,"w":8}\n',  # missing b64 — skipped
        '{"schema":1,"fmt":"jpeg","w":6,"h":4,"b64":"YmFy"}\n',
    ]
    stream = _helper.CamStream(_FakeProc(lines))
    frames = list(stream.frames())
    assert frames == [
        {"fmt": "jpeg", "w": 4, "h": 3, "b64": "Zm9v"},
        {"fmt": "jpeg", "w": 6, "h": 4, "b64": "YmFy"},
    ]


def test_camstream_close_terminates_the_proc():
    proc = _FakeProc([])
    _helper.CamStream(proc).close()
    assert proc.terminated


def test_camstream_builds_the_expected_args(monkeypatch):
    seen = {}

    def fake_popen(args, **kw):
        seen["args"] = args
        return _FakeProc([])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _helper.camstream("bin", interval=0.25, width=960, height=540, quality=0.5)
    assert seen["args"][:2] == ["bin", "camstream"]
    assert "--interval" in seen["args"] and "--width" in seen["args"]


def _fake_proc(returncode, stdout=b""):
    class _P:
        pass
    p = _P()
    p.returncode = returncode
    p.stdout = stdout
    return p


def test_camsnap_ok(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: _fake_proc(0, b'{"schema":1,"fmt":"jpeg","w":4,"h":3,"b64":"Zm9v"}'),
    )
    frame, status = _helper.camsnap("bin")
    assert status == "ok"
    assert frame == {"fmt": "jpeg", "w": 4, "h": 3, "b64": "Zm9v"}


@pytest.mark.parametrize("code,expected", [(3, "denied"), (5, "restricted"), (4, "not_determined"), (64, "unsupported"), (2, "error")])
def test_camsnap_maps_exit_codes(monkeypatch, code, expected):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(code))
    frame, status = _helper.camsnap("bin")
    assert frame is None and status == expected


# ---------- CLI: companion camera ----------

def _cli(monkeypatch, tmp_path):
    from diting import cli

    monkeypatch.setenv("DITING_COMPANION_STATE", str(tmp_path / "companion.json"))
    return cli


def test_cli_camera_on_enables_after_ok_capture(monkeypatch, tmp_path, capsys):
    cli = _cli(monkeypatch, tmp_path)
    from diting.companion import state as cstate

    cstate.PairingState.generate("https://r.example").save()
    monkeypatch.setattr(_helper, "find_helper", lambda: "bin")
    monkeypatch.setattr(_helper, "camsnap", lambda binary, **k: ({"fmt": "jpeg", "w": 4, "h": 3, "b64": "Zm9v"}, "ok"))
    cli._companion_camera(["on"])
    assert load_state().camera_enabled is True
    assert "enabled" in capsys.readouterr().out.lower()


def test_cli_camera_on_reports_unsupported_helper(monkeypatch, tmp_path, capsys):
    cli = _cli(monkeypatch, tmp_path)
    from diting.companion import state as cstate

    cstate.PairingState.generate("https://r.example").save()
    monkeypatch.setattr(_helper, "find_helper", lambda: "bin")
    monkeypatch.setattr(_helper, "camsnap", lambda binary, **k: (None, "unsupported"))
    with pytest.raises(SystemExit):
        cli._companion_camera(["on"])
    assert load_state().camera_enabled is False
    assert "make helper" in capsys.readouterr().err.lower()


def test_cli_camera_off_disables(monkeypatch, tmp_path):
    cli = _cli(monkeypatch, tmp_path)
    from diting.companion import state as cstate

    st = cstate.PairingState.generate("https://r.example")
    st.camera_enabled = True
    st.save()
    cli._companion_camera(["off"])
    assert load_state().camera_enabled is False
