/**
 * picolet-bridge-js — window.picolet IPC bridge for picolet webview apps.
 *
 * Exposes:
 *   window.picolet.invoke(cmd, args, opts?) → Promise<unknown>
 *   window.picolet.on(event, handler) → () => void
 *   window.picolet.emit(topic, data) → void
 *
 * Internal:
 *   window.__picolet_recv(jsonString) — called by the Python runtime to
 *   deliver replies and push events. Replaces the PH07 no-op stub.
 *
 *   window.picolet._drainPending(reason) — called by the host on transport
 *   close to reject all outstanding invoke() promises. Not part of the
 *   public app API; intended for host integration code.
 *
 * Wire format: docs/architecture.md §"IPC wire format".
 */

// ---------------------------------------------------------------------------
// Internal types
// ---------------------------------------------------------------------------

type PendingInvoke = {
  resolve: (value: unknown) => void;
  reject:  (error: Error)   => void;
};

type PicoletWireRequest = { id: number; cmd: string; args: unknown };
type PicoletWireEvent   = { event: string; data: unknown };

// ---------------------------------------------------------------------------
// Module-level state (inside IIFE, not exposed on window)
// ---------------------------------------------------------------------------

let _nextId = 1;
const _pending  = new Map<number, PendingInvoke>();
const _handlers = new Map<string, Set<(data: unknown) => void>>();

// ---------------------------------------------------------------------------
// Outbound postMessage helper
// ---------------------------------------------------------------------------

function _send(msg: PicoletWireRequest | PicoletWireEvent): void {
  const json = JSON.stringify(msg);
  const w: any = window;
  // Linux/WebKitGTK: the user-content manager exposes its registered
  // handlers under window.webkit.messageHandlers.<name>.
  if (w.webkit && w.webkit.messageHandlers && w.webkit.messageHandlers.picolet) {
    w.webkit.messageHandlers.picolet.postMessage(json);
    return;
  }
  // Windows/WebView2: ICoreWebView2's postMessage is exposed on
  // window.chrome.webview.  Feature-check the function presence
  // explicitly so we never call a stray non-function property.
  if (w.chrome && w.chrome.webview &&
      typeof w.chrome.webview.postMessage === "function") {
    w.chrome.webview.postMessage(json);
    return;
  }
  throw new Error("[picolet] no host postMessage channel available");
}

// ---------------------------------------------------------------------------
// Inbound receiver — replaces the PH07 no-op stub
// ---------------------------------------------------------------------------

(window as any).__picolet_recv = function(jsonString: string): void {
  let msg: Record<string, unknown>;
  try {
    msg = JSON.parse(jsonString) as Record<string, unknown>;
  } catch (e) {
    console.warn("[picolet] malformed inbound JSON:", e);
    return;
  }

  if (Number.isInteger(msg.id) && (msg.id as number) > 0 && "ok" in msg) {
    // Reply to a pending invoke.
    const pending = _pending.get(msg.id as number);
    if (!pending) {
      console.warn("[picolet] no pending invoke for id", msg.id);
      return;
    }
    _pending.delete(msg.id as number);
    if (msg.ok) {
      pending.resolve(msg.result);
    } else {
      const errInfo = msg.error as { type: string; message: string } | undefined;
      const err = new Error(errInfo?.message ?? String(msg.error));
      err.name = errInfo?.type ?? "Error";
      pending.reject(err);
    }
  } else if (typeof msg.event === "string") {
    // Push event from Python.
    const subs = _handlers.get(msg.event);
    if (subs) {
      for (const handler of Array.from(subs)) {
        try {
          handler(msg.data);
        } catch (e) {
          console.error("[picolet] on() handler threw:", e);
        }
      }
    }
  } else {
    console.warn("[picolet] unrecognised inbound message:", msg);
  }
};

// ---------------------------------------------------------------------------
// Public API — window.picolet
// ---------------------------------------------------------------------------

(window as any).picolet = {
  /**
   * Invoke a Python @picolet.command handler and await its result.
   *
   * @param cmd     Name of the registered Python command.
   * @param args    JSON-serialisable argument payload (null if omitted).
   * @param opts    Optional settings.
   * @param opts.timeout  Milliseconds before the promise rejects with
   *                      Error("invoke timeout"). Omit or pass 0 for no timeout.
   * @returns Promise that resolves with the Python return value or
   *          rejects with an Error whose .name is the Python exception
   *          type and .message is the human-readable text.
   */
  invoke(cmd: string, args: unknown = null, opts?: { timeout?: number }): Promise<unknown> {
    const id = _nextId++;
    const promise = new Promise((resolve, reject) => {
      _pending.set(id, { resolve, reject });
      if (opts?.timeout) {
        const timeoutId = setTimeout(() => {
          if (_pending.delete(id)) {
            reject(new Error("invoke timeout"));
          }
        }, opts.timeout);
        // Wrap the entry so the timeout is cancelled when the invoke
        // resolves or rejects through the normal reply path.
        const orig = _pending.get(id)!;
        _pending.set(id, {
          resolve: (v) => { clearTimeout(timeoutId); orig.resolve(v); },
          reject:  (e) => { clearTimeout(timeoutId); orig.reject(e); },
        });
      }
    });
    _send({ id, cmd, args });
    return promise;
  },

  /**
   * Subscribe to a Python push event (picolet.emit on the Python side).
   *
   * @param event   Topic name, matching the Python picolet.emit() topic.
   * @param handler Called with msg.data whenever the event arrives.
   * @returns Unsubscribe function — call it to remove the handler.
   */
  on(event: string, handler: (data: unknown) => void): () => void {
    if (!_handlers.has(event)) {
      _handlers.set(event, new Set());
    }
    _handlers.get(event)!.add(handler);
    return function unsubscribe(): void {
      _handlers.get(event)?.delete(handler);
    };
  },

  /**
   * Send a JS-push event to the Python side (picolet.on() subscribers).
   *
   * @param topic  Event topic name.
   * @param data   JSON-serialisable payload (null if omitted).
   */
  emit(topic: string, data: unknown = null): void {
    _send({ event: topic, data } as PicoletWireEvent);
  },

  /**
   * Drain all outstanding invoke() promises with a rejection.
   *
   * Intended for host integration code: call this when the transport
   * connection to the Python runtime closes unexpectedly so that callers
   * do not hang indefinitely.
   *
   * @param reason  Human-readable description of why the transport closed.
   *                Becomes the .message of each rejection Error.
   */
  _drainPending(reason: string): void {
    const err = new Error(reason);
    for (const { reject } of _pending.values()) {
      reject(err);
    }
    _pending.clear();
  },
};
