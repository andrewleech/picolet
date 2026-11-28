"""Behavioural tests for the packaged Windows executable against a fake MCP server.

Run from this example directory on WSL (Windows interop) or Windows: pytest tests
The exe is built beforehand with scripts/build.sh.
"""
import json, os, queue, subprocess, threading, time
from pathlib import Path
import pytest
from hub_peer import State, make

EXE = Path(os.environ.get("ADAPTER_EXE", Path(__file__).parent.parent / "target/windows-x64/mcp-stdio-http.exe"))
PORT = int(os.environ.get("ADAPTER_TEST_PORT", "28411"))
pytestmark = pytest.mark.skipif(not EXE.exists(), reason="adapter exe not built")


@pytest.fixture()
def hub():
    st = State()
    srv = make(st, PORT)
    yield st
    srv.shutdown(); srv.server_close()


class Client:
    def __init__(self, port=PORT, extra=()):
        self.p = subprocess.Popen([str(EXE), f"http://127.0.0.1:{port}/mcp", *extra], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.q = queue.Queue()
        self.err = b""
        threading.Thread(target=self._rd, daemon=True).start()
        threading.Thread(target=self._rde, daemon=True).start()

    def _rd(self):
        for line in self.p.stdout:
            self.q.put(json.loads(line))
        self.q.put(None)

    def _rde(self):
        self.err = self.p.stderr.read()

    def send(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode()); self.p.stdin.flush()

    def get(self, timeout=10):
        return self.q.get(timeout=timeout)

    def handshake(self):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        r = self.get()
        assert r["result"]["protocolVersion"] == "2025-06-18"
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def close(self):
        try: self.p.stdin.close()
        except OSError: pass
        try: return self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill(); raise


def test_handshake_unicode_headers_delete(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": "a", "method": "tools/list"})
    r = c.get()
    assert r["id"] == "a" and r["result"]["tools"][0]["name"] == "t\u00e9st\u2603"
    assert c.close() == 0
    posts = [x for x in hub.log if x[0] == "POST"]
    assert posts[1][2]["MCP-Session-Id"] == "sess-1" and posts[1][2]["MCP-Protocol-Version"] == "2025-06-18"
    assert "MCP-Session-Id" not in posts[0][2]
    assert hub.deleted and [x for x in hub.log if x[0] == "DELETE"][0][2]["MCP-Session-Id"] == "sess-1"


def test_concurrent_slow_and_fast(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 10, "method": "tools/slow"})
    c.send({"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
    first = c.get(); second = c.get()
    assert first["id"] == 11 and second["id"] == 10
    c.close()


def test_string_and_int_ids_distinct(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 5, "method": "tools/echo", "params": {"v": 1}})
    c.send({"jsonrpc": "2.0", "id": "5", "method": "tools/echo", "params": {"v": 2}})
    got = {json.dumps(c.get()["id"]): 1 for _ in range(2)}
    assert set(got) == {"5", '"5"'}
    c.close()


def test_post_sse_progress_then_result(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 20, "method": "tools/stream"})
    a = c.get(); b = c.get()
    assert a["method"] == "notifications/progress" and b["result"] == {"ok": True}
    c.close()


def test_reverse_request_roundtrip(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 30, "method": "tools/reverse"})
    req = c.get(); assert req["method"] == "roots/list" and req["id"] == 900
    c.send({"jsonrpc": "2.0", "id": 900, "result": {"roots": []}})
    assert c.get()["result"] == {"replied": True}
    c.close()


def test_get_stream_notification_and_reconnect_last_event_id(hub):
    c = Client(); c.handshake()
    assert hub.get_open.wait(5)
    hub.get_events.append((3, {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}))
    assert c.get()["method"] == "notifications/tools/list_changed"
    hub.get_events.append("CLOSE")
    deadline = time.time() + 6
    while time.time() < deadline and len([x for x in hub.log if x[0] == "GET"]) < 2:
        time.sleep(0.1)
    gets = [x for x in hub.log if x[0] == "GET"]
    assert len(gets) >= 2 and gets[1][2].get("Last-Event-ID") == "3"
    c.close()


def test_hung_response_reports_unknown_outcome_and_no_replay(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 40, "method": "tools/hang"})
    r = c.get()
    assert r["id"] == 40 and "unknown" in r["error"]["message"]
    assert len([x for x in hub.log if x[0] == "POST" and x[3].get("id") == 40]) == 1
    c.close()


def test_http_500_becomes_error_response(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 50, "method": "tools/error500"})
    r = c.get(); assert r["id"] == 50 and "500" in r["error"]["message"] and "boom" in r["error"]["message"]
    c.close()


def test_large_payload_both_directions(hub):
    c = Client(); c.handshake()
    n = 3_000_000
    c.send({"jsonrpc": "2.0", "id": 60, "method": "tools/big", "params": {"n": n}})
    r = c.get(30); assert len(r["result"]["blob"]) == n
    c.send({"jsonrpc": "2.0", "id": 61, "method": "tools/echo", "params": {"s": "y" * n}})
    r = c.get(30); assert len(r["result"]["s"]) == n
    c.close()


def test_cancel_forwarded(hub):
    c = Client(); c.handshake()
    c.send({"jsonrpc": "2.0", "id": 70, "method": "tools/slow"})
    c.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 70}})
    deadline = time.time() + 5
    while time.time() < deadline and not hub.cancelled: time.sleep(0.05)
    assert hub.cancelled
    c.get(); c.close()


def test_hub_down_fails_initialize_after_timeout():
    c = Client(port=28999, extra=("--connect-timeout", "1"))
    c.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    t = time.time(); r = c.get(15)
    assert r["id"] == 1 and "Cannot communicate with MCP server" in r["error"]["message"] and time.time() - t < 10
    assert c.close() == 1


def test_first_message_must_be_initialize(hub):
    c = Client(); c.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert c.close() == 1


@pytest.mark.parametrize("url", ["http://localhost:1/mcp", "http://127.0.0.1/mcp", "http://u:p@127.0.0.1:1/mcp", "http://127.0.0.1:0/mcp", "http://127.0.0.1:1/mcp#f", "https://127.0.0.1:1/mcp"])
def test_url_validation(url):
    r = subprocess.run([str(EXE), url], capture_output=True, timeout=10)
    assert r.returncode == 2


def test_ten_mb_result_is_forwarded(hub):
    c = Client(); c.handshake()
    n = 10_000_000
    c.send({"jsonrpc": "2.0", "id": 80, "method": "tools/big", "params": {"n": n}})
    r = c.get(60); assert r["id"] == 80 and len(r["result"]["blob"]) == n
    c.close()


@pytest.fixture()
def isolated_hub():
    state = State()
    server = make(state, 0, host="127.0.0.1")
    try:
        yield state, server.server_address[1]
    finally:
        state.slow_release.set()
        server.shutdown()
        server.server_close()


def test_gated_slow_hub_does_not_block_other_requests(isolated_hub):
    state, port = isolated_hub
    client = Client(port=port)
    try:
        client.handshake()
        client.send({"jsonrpc": "2.0", "id": 10, "method": "tools/gated-slow"})
        assert state.slow_started.wait(5)
        client.send({"jsonrpc": "2.0", "id": 11, "method": "tools/list"})
        fast = client.get(timeout=5)
        assert fast["id"] == 11
        assert fast["result"]["tools"][0]["name"] == "t\u00e9st\u2603"
        state.slow_release.set()
        assert client.get(timeout=5) == {"jsonrpc": "2.0", "id": 10, "result": {"slow": True}}
        assert client.close() == 0
    finally:
        state.slow_release.set()
        if client.p.poll() is None:
            client.p.kill()
            client.p.wait()


def test_stalled_stdout_does_not_block_hub_requests(isolated_hub):
    state, port = isolated_hub
    process = subprocess.Popen(
        [str(EXE), f"http://127.0.0.1:{port}/mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    lines = queue.Queue()

    def read_one():
        lines.put(process.stdout.readline())

    def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    try:
        threading.Thread(target=read_one, daemon=True).start()
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert json.loads(lines.get(timeout=10))["result"]["protocolVersion"] == "2025-06-18"
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # No reader runs while this response fills the stdout pipe.
        send({"jsonrpc": "2.0", "id": 10, "method": "tools/backpressure", "params": {"n": 10 * 1024 * 1024}})
        assert state.big_started.wait(5)
        time.sleep(1)
        send({"jsonrpc": "2.0", "id": 11, "method": "tools/observed"})
        assert state.observed_request.wait(5), "stdout backpressure blocked the next MCP server request"

        # Resume the consumer and verify both complete, intact JSON-RPC responses.
        threading.Thread(target=read_one, daemon=True).start()
        first = json.loads(lines.get(timeout=20))
        threading.Thread(target=read_one, daemon=True).start()
        second = json.loads(lines.get(timeout=20))
        responses = {response["id"]: response for response in (first, second)}
        assert responses[10]["result"]["blob"] == "x" * (10 * 1024 * 1024)
        assert responses[11]["result"] == {"ok": True}
        process.stdin.close()
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
