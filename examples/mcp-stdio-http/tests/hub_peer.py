"""Controllable fake MCP streamable-HTTP server for the packaged-binary tests."""
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class State:
    def __init__(self):
        self.log = []          # (method, path, headers dict, body)
        self.deleted = False
        self.get_events = []   # queued server->client messages for GET stream
        self.lock = threading.Lock()
        self.get_open = threading.Event()
        self.reverse_replies = []
        self.cancelled = []
        self.slow_started = threading.Event()
        self.slow_release = threading.Event()
        self.big_started = threading.Event()
        self.observed_request = threading.Event()

def make(state, port, host="0.0.0.0"):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def _chunk(self, data):
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n"); self.wfile.flush()
        def _json(self, obj, extra=None):
            body = json.dumps(obj).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items(): self.send_header(k, v)
            self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(body)
            self.close_connection = True
        def _sse_start(self):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked"); self.send_header("Connection", "close")
            self.end_headers()
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n)
            msg = json.loads(raw)
            with state.lock: state.log.append(("POST", self.path, dict(self.headers), msg))
            m = msg.get("method"); rid = msg.get("id")
            if m == "initialize":
                self._json({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}}}, {"MCP-Session-Id": "sess-1"})
            elif m == "tools/list":
                self._json({"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "t\u00e9st\u2603"}]}})
            elif m == "tools/echo":
                self._json({"jsonrpc": "2.0", "id": rid, "result": msg.get("params")})
            elif m == "tools/stream":
                self._sse_start()
                self._chunk(b"data: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}).encode() + b"\n\n")
                time.sleep(0.4)
                self._chunk(b"id: 7\ndata: " + json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}).encode() + b"\n\n")
                self.wfile.write(b"0\r\n\r\n"); self.close_connection = True
            elif m == "tools/reverse":
                self._sse_start()
                self._chunk(b"data: " + json.dumps({"jsonrpc": "2.0", "id": 900, "method": "roots/list"}).encode() + b"\n\n")
                deadline = time.time() + 5
                while time.time() < deadline:
                    with state.lock:
                        if state.reverse_replies: break
                    time.sleep(0.05)
                self._chunk(b"data: " + json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"replied": bool(state.reverse_replies)}}).encode() + b"\n\n")
                self.wfile.write(b"0\r\n\r\n"); self.close_connection = True
            elif m == "tools/hang":
                self._sse_start()
                time.sleep(1.0)
                self.close_connection = True   # close with no answer, no terminator
            elif m == "tools/big":
                self._json({"jsonrpc": "2.0", "id": rid, "result": {"blob": "x" * msg["params"]["n"]}})
            elif m == "tools/gated-slow":
                state.slow_started.set()
                if state.slow_release.wait(10):
                    self._json({"jsonrpc": "2.0", "id": rid, "result": {"slow": True}})
            elif m == "tools/backpressure":
                state.big_started.set()
                try:
                    self._json({"jsonrpc": "2.0", "id": rid, "result": {"blob": "x" * msg["params"]["n"]}})
                except OSError:
                    pass
            elif m == "tools/observed":
                state.observed_request.set()
                self._json({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}})
            elif m == "tools/slow":
                time.sleep(1.5)
                self._json({"jsonrpc": "2.0", "id": rid, "result": {"slow": True}})
            elif m == "tools/error500":
                body = b"boom"; self.send_response(500); self.send_header("Content-Length", "4"); self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(body); self.close_connection = True
            else:
                if m == "notifications/cancelled":
                    with state.lock: state.cancelled.append(msg)
                if m is None:
                    with state.lock: state.reverse_replies.append(msg)
                self.send_response(202); self.send_header("Content-Length", "0"); self.send_header("Connection", "close"); self.end_headers(); self.close_connection = True
        def do_GET(self):
            with state.lock: state.log.append(("GET", self.path, dict(self.headers), None))
            self._sse_start(); state.get_open.set()
            try:
                t0 = time.time()
                while time.time() - t0 < 30:
                    with state.lock:
                        ev = state.get_events.pop(0) if state.get_events else None
                    if ev is None:
                        time.sleep(0.05); continue
                    if ev == "CLOSE": break
                    self._chunk(b"id: " + str(ev[0]).encode() + b"\ndata: " + json.dumps(ev[1]).encode() + b"\n\n")
                self.wfile.write(b"0\r\n\r\n")
            except OSError: pass
            self.close_connection = True
        def do_DELETE(self):
            with state.lock:
                state.log.append(("DELETE", self.path, dict(self.headers), None)); state.deleted = True
            self.send_response(204); self.send_header("Connection", "close"); self.end_headers(); self.close_connection = True
    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
