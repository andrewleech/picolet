/**
 * picolet.d.ts — ambient TypeScript declaration for window.picolet.
 *
 * Hand-authored: picolet-bridge-js builds as an IIFE (no ES module export),
 * so tsc --declaration produces nothing useful.  This file augments the
 * global Window interface so Vue/TS apps get typed window.picolet calls
 * without an explicit import.
 *
 * Reference from a Vue project:
 *   src/env.d.ts:  /// <reference types="picolet-bridge-js" />
 * or add to tsconfig.json:
 *   "types": ["picolet-bridge-js"]
 *
 * FR-VUE-3
 */

export {};

declare global {
  interface PicoletBridge {
    /**
     * Invoke a Python @picolet.command handler and await its result.
     *
     * @param cmd   Name of the registered Python command.
     * @param args  JSON-serialisable argument payload (null if omitted).
     * @param opts  Optional settings.
     * @param opts.timeout  Milliseconds before the promise rejects with
     *                      Error("invoke timeout"). Omit or 0 for no timeout.
     * @returns Promise resolving with the Python return value, or rejecting
     *          with an Error whose .name is the Python exception type.
     */
    invoke(cmd: string, args?: unknown, opts?: { timeout?: number }): Promise<unknown>;

    /**
     * Subscribe to a Python push event (emitted by picolet.emit on the Python side).
     *
     * @param event   Topic name, matching the Python picolet.emit() topic.
     * @param handler Called with the event data whenever the event arrives.
     * @returns Unsubscribe function — call to remove this handler.
     */
    on(event: string, handler: (data: unknown) => void): () => void;

    /**
     * Send a JS-push event to the Python side (received by picolet.on() subscribers).
     *
     * @param topic  Event topic name.
     * @param data   JSON-serialisable payload (null if omitted).
     */
    emit(topic: string, data?: unknown): void;

    /**
     * Drain all outstanding invoke() promises with a rejection.
     *
     * Intended for host integration code: call when the transport closes
     * unexpectedly so callers do not hang indefinitely.
     *
     * @param reason  Human-readable description included in each rejection Error.
     */
    _drainPending(reason: string): void;

    /**
     * True once the bridge IIFE has fully executed. AppHarness polls this
     * before running test assertions.
     */
    readonly __ready__: boolean;
  }

  interface Window {
    picolet: PicoletBridge;
  }
}
