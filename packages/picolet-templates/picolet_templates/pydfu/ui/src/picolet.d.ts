/**
 * picolet.d.ts — ambient TypeScript declaration for window.picolet.
 *
 * Declares the window.picolet bridge surface so Vue/TS components get typed access
 * to invoke, on, emit, and _drainPending without any extra import.
 *
 * FR-VUE-3
 */

export {};

declare global {
  interface PicoletBridge {
    invoke(cmd: string, args?: unknown, opts?: { timeout?: number }): Promise<unknown>;
    on(event: string, handler: (data: unknown) => void): () => void;
    emit(topic: string, data?: unknown): void;
    _drainPending(reason: string): void;
    readonly __ready__: boolean;
  }

  interface Window {
    picolet: PicoletBridge;
  }
}
