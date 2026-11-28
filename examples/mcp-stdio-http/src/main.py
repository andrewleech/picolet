"""Bridge newline-delimited MCP stdio to a streamable-HTTP server session."""

import asyncio
import gc
import json
import sys
import time

import hconn
import shallow

_CHUNK = 16384
_HEAP_STEP = 64 * 1024 * 1024
_HEAP_LOW = 48 * 1024 * 1024
_HEAP_MAX = 1024 * 1024 * 1024
_heap_total = 0


def grow_heap():
    global _heap_total
    if _heap_total < _HEAP_MAX and gc.mem_free() < _HEAP_LOW:
        try:
            gc.add_heap(_HEAP_STEP)
            _heap_total += _HEAP_STEP
        except MemoryError:
            _heap_total = _HEAP_MAX


def log(msg):
    try:
        sys.stderr.write("mcp-stdio-http: " + msg + "\n")
    except OSError:
        pass


def parse_url(url):
    for ch in url:
        if not 0x21 <= ord(ch) <= 0x7e:
            raise ValueError("URL must contain only printable ASCII without spaces")
    prefix = "http://127.0.0.1:"
    if not url.startswith(prefix):
        raise ValueError("URL must use http://127.0.0.1 with an explicit port and no credentials")
    rest = url[len(prefix):]
    if "#" in rest:
        raise ValueError("URL must not contain a fragment")
    slash = rest.find("/")
    port_s = rest if slash < 0 else rest[:slash]
    path = "/" if slash < 0 else rest[slash:]
    if not port_s or not port_s.isdigit():
        raise ValueError("URL must use http://127.0.0.1 with an explicit port")
    port = int(port_s)
    if not 0 < port <= 65535:
        raise ValueError("invalid HTTP port")
    return port, path


def key_of(value):
    return json.dumps(value)


def dumps(obj):
    return json.dumps(obj).encode("utf-8")


class SseParser:
    def __init__(self, adapter, init=False, event_ids=False):
        self.adapter, self.init, self.event_ids = adapter, init, event_ids
        self.buf = bytearray()
        self.scan = 0
        self.data = []
        self.event = self.event_id = None

    async def feed(self, chunk):
        self.buf.extend(chunk)
        while True:
            idx = self.buf.find(b"\n", self.scan)
            if idx < 0:
                self.scan = len(self.buf)
                return
            line = bytes(self.buf[:idx])
            self.buf = self.buf[idx + 1:]
            self.scan = 0
            await self.line(line)

    async def flush(self):
        if self.buf:
            await self.line(bytes(self.buf))
            self.buf = bytearray()

    async def line(self, raw):
        try:
            line = raw.decode("utf-8").rstrip("\r")
        except UnicodeError:
            raise ValueError("MCP server returned invalid UTF-8 in an event stream") from None
        if not line:
            if self.event_id is not None and self.event_ids:
                self.adapter.last_event_id = self.event_id
            if self.data and self.event in (None, "message"):
                raw = "\n".join(self.data).encode("utf-8")
                try:
                    obj = shallow.loads_msg(raw)
                except (ValueError, UnicodeError):
                    raise ValueError("MCP server returned invalid JSON in an event stream") from None
                await self.adapter.receive(obj, raw, self.init)
            self.data, self.event, self.event_id = [], None, None
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                self.data.append(value)
            elif field == "event":
                self.event = value
            elif field == "id" and "\x00" not in value:
                self.event_id = value


class Adapter:
    def __init__(self, url, timeout):
        self.port, self.path = parse_url(url)
        self.url, self.timeout = url, timeout
        self.session = self.version = self.last_event_id = None
        self.phase = 0
        self.pending, self.submissions = {}, {}
        self.held, self.tasks = [], []
        self.output_lock = asyncio.Lock()
        self.stdin = asyncio.StreamReader(sys.stdin.buffer)
        self.stdout = asyncio.StreamWriter(sys.stdout.buffer)
        self.exit_code = 0
        self.stopping = False
        self.input_task = None
        self.init_done = asyncio.Event()

    def spawn(self, coro):
        async def runner():
            try:
                await coro
            finally:
                self.tasks.remove(task)
        task = asyncio.create_task(runner())
        self.tasks.append(task)
        return task

    async def emit(self, raw):
        async with self.output_lock:
            try:
                view = memoryview(raw)
                for off in range(0, len(view), _CHUNK):
                    self.stdout.write(view[off:off + _CHUNK])
                    await self.stdout.drain()
                self.stdout.write(b"\n")
                await self.stdout.drain()
            except OSError as exc:
                await self.fatal("Client output disconnected: " + str(exc), False)

    async def receive(self, obj, raw, initialize=False):
        if not isinstance(obj, dict):
            raise ValueError("MCP server returned a non-object JSON-RPC message")
        if initialize and isinstance(obj.get("result"), dict):
            ver = obj["result"].get("protocolVersion")
            if isinstance(ver, str) and ver:
                self.version = ver
                if self.phase < 2:
                    self.phase = 2
                    if self.session:
                        self.spawn(self.get_events())
                    self.flush_held()
        if "method" not in obj and "id" in obj:
            self.pending.pop(key_of(obj["id"]), None)
        if b"\n" in raw or b"\r" in raw:
            raw = raw.replace(b"\r", b" ").replace(b"\n", b" ")
        await self.emit(raw)

    async def fail_request(self, rid, detail):
        if key_of(rid) in self.pending:
            del self.pending[key_of(rid)]
            await self.emit(dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": detail}}))

    async def fatal(self, detail, notify=True):
        if self.stopping:
            return
        self.stopping, self.exit_code = True, 1
        log(detail)
        if notify:
            ids = list(self.pending.values())
            for rid in ids:
                await self.fail_request(rid, detail)
        if self.input_task:
            self.input_task.cancel()

    def headers(self, accept=True):
        headers = {}
        if accept:
            headers["Accept"] = "application/json, text/event-stream"
        if self.session:
            headers["MCP-Session-Id"] = self.session
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        return headers

    def unreachable(self, exc):
        return "Cannot communicate with MCP server at " + self.url + ": " + str(exc) + ". Ensure the server is running; an interrupted operation may have an unknown outcome."

    def flush_held(self):
        held, self.held = self.held, []
        for raw, obj in held:
            self.dispatch(raw, obj)

    def dispatch(self, raw, obj):
        method = obj.get("method")
        if self.phase < 2 or (self.phase == 2 and method != "notifications/initialized"):
            self.held.append((raw, obj))
            return
        has_request = method is not None and "id" in obj
        if has_request:
            self.pending[key_of(obj["id"])] = obj["id"]
        target = None
        if method == "notifications/cancelled":
            params = obj.get("params")
            if isinstance(params, dict) and "requestId" in params:
                target = self.submissions.get(key_of(params["requestId"]))
        self.start_post(raw, obj, False, target)

    def start_post(self, raw, obj, initialize, target=None):
        headers = self.headers()
        headers["Content-Type"] = "application/json"
        conn = hconn.HttpConn(self.port, "POST", self.path, headers, raw)
        has_request = "method" in obj and "id" in obj
        if has_request:
            self.submissions[key_of(obj["id"])] = conn
        self.spawn(self.post(conn, raw, obj, initialize, target))

    async def post(self, conn, raw, obj, initialize, target):
        rid = obj.get("id")
        has_request = "method" in obj and "id" in obj
        deadline = time.ticks_add(time.ticks_ms(), self.timeout)
        invalid_session = False
        try:
            if target is not None:
                await target.sent.wait()
            while True:
                try:
                    remaining = time.ticks_diff(deadline, time.ticks_ms())
                    if initialize:
                        await asyncio.wait_for_ms(conn.open(), max(1, remaining))
                    else:
                        await conn.open()
                    if not initialize or conn.status != 503 or remaining <= 0:
                        break
                except OSError:
                    if not initialize or time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                        raise
                await conn.close()
                await asyncio.sleep_ms(100)
                conn = hconn.HttpConn(self.port, "POST", self.path, conn.headers, raw)
                if has_request:
                    self.submissions[key_of(rid)] = conn
            status = conn.status
            if initialize:
                self.session = conn.response_headers.get("mcp-session-id")
            if status not in (200, 202, 204):
                body = bytearray()
                while len(body) < 4096:
                    part = await conn.read_body()
                    if not part:
                        break
                    body.extend(part[:4096 - len(body)])
                invalid_session = status in (404, 410)
                detail = "HTTP " + str(status) + " " + conn.reason
                if body:
                    detail += ": " + bytes(body).decode("utf-8", "replace")
                raise ValueError(detail)
            if status == 200:
                ctype = conn.response_headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ("application/json", "text/event-stream"):
                    raise ValueError("unexpected MCP server response Content-Type: " + (ctype or "(missing)"))
                body = bytearray()
                parser = SseParser(self, initialize) if ctype == "text/event-stream" else None
                while True:
                    grow_heap()
                    part = await conn.read_body()
                    if not part:
                        break
                    if parser:
                        await parser.feed(part)
                    else:
                        body.extend(part)
                if parser:
                    await parser.flush()
                else:
                    data = bytes(body)
                    del body
                    try:
                        msg = shallow.loads_msg(data)
                    except (ValueError, UnicodeError):
                        raise ValueError("MCP server returned invalid JSON") from None
                    await self.receive(msg, data, initialize)
            if has_request and key_of(rid) in self.pending:
                raise ValueError("MCP server closed the response without answering the request; outcome may be unknown")
            if initialize and not self.version:
                raise ValueError("initialize did not negotiate an MCP protocol version")
            if obj.get("method") == "notifications/initialized":
                self.phase = 3
                self.flush_held()
        # MicroPython defines its own asyncio.TimeoutError, distinct from the built-in.
        except (OSError, ValueError, UnicodeError, asyncio.TimeoutError) as exc:  # noqa: UP041
            detail = self.unreachable(exc)
            if has_request:
                await self.fail_request(rid, detail)
            if initialize or invalid_session or not has_request:
                await self.fatal(detail)
            else:
                log(detail)
        finally:
            await conn.close()
            if has_request and self.submissions.get(key_of(rid)) is conn:
                del self.submissions[key_of(rid)]
            if initialize:
                self.init_done.set()

    async def get_events(self):
        delay = 100
        while not self.stopping:
            headers = self.headers(False)
            headers["Accept"] = "text/event-stream"
            if self.last_event_id:
                headers["Last-Event-ID"] = self.last_event_id
            conn = hconn.HttpConn(self.port, "GET", self.path, headers)
            try:
                await conn.open()
                if conn.status == 405:
                    return
                if conn.status in (404, 410):
                    raise ValueError("session expired (HTTP " + str(conn.status) + "); reinitialize the client")
                if conn.status != 200:
                    raise ValueError("event stream returned HTTP " + str(conn.status) + " " + conn.reason)
                ctype = conn.response_headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype != "text/event-stream":
                    raise ValueError("unexpected Content-Type: " + (ctype or "(missing)"))
                parser = SseParser(self, event_ids=True)
                while True:
                    grow_heap()
                    part = await conn.read_body()
                    if not part:
                        break
                    await parser.feed(part)
                await parser.flush()
            except (OSError, ValueError, UnicodeError) as exc:
                await self.fatal("MCP server event stream at " + self.url + " failed: " + str(exc) + ". Reconnect after checking the server; in-flight operations may have an unknown outcome.")
                return
            finally:
                await conn.close()
            await asyncio.sleep_ms(delay)
            delay = min(delay * 2, 2000)

    async def read_input(self):
        buf = bytearray()
        scan = 0
        while not self.stopping:
            grow_heap()
            part = await self.stdin.read(_CHUNK)
            if not part:
                break
            buf.extend(part)
            while True:
                idx = buf.find(b"\n", scan)
                if idx < 0:
                    scan = len(buf)
                    break
                raw = bytes(buf[:idx]).rstrip(b"\r")
                buf = buf[idx + 1:]
                scan = 0
                if not raw.strip():
                    continue
                try:
                    obj = shallow.loads_msg(raw)
                    if not isinstance(obj, dict):
                        raise ValueError("expected a JSON-RPC object")
                except (ValueError, UnicodeError) as exc:
                    await self.fatal("Invalid stdio JSON-RPC message: " + str(exc))
                    return
                if self.phase == 0:
                    if obj.get("method") != "initialize" or "id" not in obj:
                        await self.fatal("The first client message must be an initialize request", False)
                        return
                    self.phase = 1
                    self.pending[key_of(obj["id"])] = obj["id"]
                    self.start_post(raw, obj, True)
                else:
                    self.dispatch(raw, obj)
        if buf.strip() and not self.stopping:
            await self.fatal("Invalid stdio JSON-RPC message: incomplete line")

    async def cleanup(self):
        if not self.session:
            return
        conn = hconn.HttpConn(self.port, "DELETE", self.path, self.headers(False))
        try:
            await asyncio.wait_for_ms(conn.open(), self.timeout)
            if conn.status not in (200, 202, 204, 404, 405, 410):
                log("session cleanup returned HTTP " + str(conn.status) + " " + conn.reason)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:  # noqa: UP041
            log("session cleanup failed: " + str(exc))
        finally:
            await conn.close()

    async def run(self):
        self.input_task = asyncio.create_task(self.read_input())
        try:
            await self.input_task
        except asyncio.CancelledError:
            pass
        if not self.stopping and self.phase == 1:
            try:
                await asyncio.wait_for_ms(self.init_done.wait(), self.timeout)
            except asyncio.TimeoutError:  # noqa: UP041
                pass
        self.stopping = True
        for task in list(self.tasks):
            task.cancel()
        # Let cancelled tasks close their sockets before deleting the session.
        await asyncio.sleep_ms(0)
        await self.cleanup()
        return self.exit_code


def main():
    args = sys.argv[1:]
    timeout, url, i = 5000, None, 0
    while i < len(args):
        arg = args[i]
        if arg == "--connect-timeout":
            i += 1
            try:
                secs = float(args[i])
            except (IndexError, ValueError):
                log("--connect-timeout needs a number of seconds")
                return 2
            if not 0 < secs <= 3600:
                log("--connect-timeout must be positive and at most 3600")
                return 2
            timeout = int(secs * 1000)
        elif url is None:
            url = arg
        else:
            log("unexpected argument: " + arg)
            return 2
        i += 1
    if url is None:
        log("usage: mcp-stdio-http URL [--connect-timeout SECONDS]")
        return 2
    try:
        grow_heap()
        adapter = Adapter(url, timeout)
    except ValueError as exc:
        log(str(exc))
        return 2
    return asyncio.run(adapter.run())


sys.exit(main())
