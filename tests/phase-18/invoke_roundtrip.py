# tests/phase-18/invoke_roundtrip.py
#
# AppHarness round-trip test: invoke 'ping' against the with-vue binary.
#
# Run via:
#   picolet test --no-build --run tests/phase-18/invoke_roundtrip.py \
#              examples/with-vue/target/linux-x64/with-vue
#
# The 'binary' and 'harness' names are injected into this script's global
# namespace by test_cmd._run_user_script (the PH17 --run contract).
#
# NOTE: this script is exec()'d inside an already-running asyncio event loop
# (test_cmd._async_main). Do NOT call asyncio.run() here.  Use
# 'raise SystemExit(rc)' to communicate the exit code.

import asyncio as _asyncio

# harness and binary are injected by test_cmd
if harness.page is None:  # noqa: F821
    print("SKIP: harness.page is None (no WebKit inspector available)")
    raise SystemExit(0)


async def _run():
    result = await harness.page.evaluate(  # noqa: F821
        "window.picolet.invoke('ping', { ts: 12345 })"
    )
    if not isinstance(result, dict):
        print(f"FAIL: expected dict result, got {type(result).__name__}: {result!r}")
        raise SystemExit(1)
    pong = result.get("pong")
    if pong != 12345:
        print(f"FAIL: expected pong=12345, got {pong!r}")
        raise SystemExit(1)
    print(f"invoke round-trip: OK (pong={pong})")
    raise SystemExit(0)


_asyncio.get_event_loop().run_until_complete(_run())
