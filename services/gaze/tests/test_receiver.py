"""The receiver client against a stand-in HTTP server."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from gaze.fixations import Fixation
from gaze.receiver import Receiver


@pytest.fixture
def server() -> Iterator[tuple[str, list[dict], dict]]:
    calls: list[dict] = []
    answer = {"code": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "auth": self.headers["Authorization"], "body": body})
            self.send_response(answer["code"])
            self.end_headers()
            self.wfile.write(json.dumps(answer.get("body", {})).encode())

        def log_message(self, *_: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", calls, answer
    httpd.shutdown()


def test_fixations_are_sent_once_and_dropped_while_paused(server: tuple, tmp_path: Path) -> None:
    url, calls, answer = server
    (tmp_path / ".token").write_text("secret\n")
    receiver = Receiver(url, tmp_path / ".token")
    receiver.add(Fixation(1_800_000_000.0, 1_800_000_000.4, 0.25, 0.5, 6))
    assert receiver.send({"tracking": True}) == "ok"
    sent = calls[-1]
    assert sent["path"] == "/gaze" and sent["auth"] == "Bearer secret"
    assert (
        sent["body"]["fixations"][0]["t"] == "2027-01-15T08:00:00+00:00" and sent["body"]["fixations"][0]["x"] == 0.25
    )
    assert receiver.send({"tracking": True}) == "ok" and calls[-1]["body"]["fixations"] == []
    answer["code"] = 500
    receiver.add(Fixation(1_800_000_001.0, 1_800_000_001.4, 0.3, 0.5, 6))
    assert receiver.send({"tracking": True}) == "refused" and len(receiver.queue) == 1  # kept for later
    answer["code"] = 409
    assert receiver.send({"tracking": True}) == "paused" and len(receiver.queue) == 0  # nothing kept while paused


def test_an_unreachable_receiver_keeps_the_queue(tmp_path: Path) -> None:
    (tmp_path / ".token").write_text("secret")
    receiver = Receiver("http://127.0.0.1:9", tmp_path / ".token", timeout=0.5)
    receiver.add(Fixation(1.0, 1.4, 0.3, 0.5, 6))
    assert receiver.send({"tracking": True}) == "offline" and len(receiver.queue) == 1


def test_the_live_point_streams_only_when_the_receiver_asks(server: tuple, tmp_path: Path) -> None:
    url, calls, answer = server
    (tmp_path / ".token").write_text("secret")
    receiver = Receiver(url, tmp_path / ".token")
    receiver.send_live(1_800_000_000.0, 0.5, 0.5)  # no confirmed token yet: nothing sent
    assert calls == []
    assert receiver.send({"tracking": True}) == "ok" and receiver.live is False
    answer["body"] = {"accepted": 0, "live": True}
    assert receiver.send({"tracking": True}) == "ok" and receiver.live is True
    receiver.send_live(1_800_000_000.0, 0.25, 0.75)
    assert receiver.flush()
    assert calls[-1] == {"path": "/gaze/live", "auth": "Bearer secret",
                         "body": {"t": "2027-01-15T08:00:00+00:00", "x": 0.25, "y": 0.75}}  # fmt: skip
    answer["code"] = 404  # an older receiver without the endpoint
    receiver.send_live(1_800_000_000.1, 0.25, 0.75)
    assert receiver.flush()
    assert receiver.live is False


def test_live_points_never_wait_and_the_newest_wins(server: tuple, tmp_path: Path) -> None:
    """A burst of points while one is in flight: send_live returns at once, and the point that
    goes out next is the newest, not a backlog."""
    import time

    url, calls, answer = server
    (tmp_path / ".token").write_text("secret")
    receiver = Receiver(url, tmp_path / ".token")
    answer["body"] = {"live": True}
    assert receiver.send({"tracking": True}) == "ok"
    started = time.perf_counter()
    for i in range(200):
        receiver.send_live(1_800_000_000.0 + i / 30, i / 1000, 0.5)
    assert time.perf_counter() - started < 0.05  # 200 calls, no network wait
    assert receiver.flush()
    live = [c for c in calls if c["path"] == "/gaze/live"]
    assert live[-1]["body"]["x"] == 0.199 and len(live) < 200
    assert len(receiver.live_ms) == len(live)


def test_the_receiver_says_whether_to_draw_the_ring_and_how_to_correct_it(server: tuple, tmp_path: Path) -> None:
    url, calls, answer = server
    (tmp_path / ".token").write_text("secret")
    receiver = Receiver(url, tmp_path / ".token")
    grid = {"cols": 2, "rows": 2, "dx": [0.01, 0.01, 0.01, 0.01], "dy": [0, 0, 0, 0]}
    answer["body"] = {"accepted": 0, "live": False, "ring": True, "offset_grid": grid}
    assert receiver.send({"tracking": True}) == "ok"
    assert receiver.ring is True and receiver.offset_grid == {**grid, "dx": [0.01] * 4, "dy": [0.0] * 4}
    answer["body"] = {"accepted": 0, "ring": True, "offset_grid": {"cols": 2}}  # malformed: no correction
    assert receiver.send({"tracking": True}) == "ok" and receiver.ring and receiver.offset_grid is None
    answer["code"] = 409  # paused: hide it
    assert receiver.send({"tracking": True}) == "paused" and receiver.ring is False


def test_line_labels_in_a_reply_are_kept_for_the_learner_and_bad_ones_dropped(server: tuple, tmp_path: Path) -> None:
    url, calls, answer = server
    (tmp_path / ".token").write_text("secret")
    receiver = Receiver(url, tmp_path / ".token")
    answer["body"] = {"lines": [
        {"id": "a", "y": 0.4, "x0": 0.1, "x1": 0.6},
        {"id": "b", "y": "x", "x0": 0.1, "x1": 0.6},  # malformed
        {"id": "c", "y": 9.0, "x0": 0.1, "x1": 0.6},  # off any monitor
        {"y": 0.4, "x0": 0.1, "x1": 0.6},  # no fixation named
    ]}  # fmt: skip
    assert receiver.send({"tracking": True}) == "ok"
    assert receiver.take_lines() == [{"id": "a", "y": 0.4, "x0": 0.1, "x1": 0.6}]
    assert receiver.take_lines() == []
