import time

import micropython


def parse_port(value: int) -> str:
    return str(value)


def device_delay() -> int:
    time.sleep_ms(1)
    return micropython.const(2) + time.ticks_diff(time.ticks_ms(), time.ticks_ms())
