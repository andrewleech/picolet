# pydfu_mock.py — mock USB adapter for tests and development.
# Activated when PICOLET_PYDFU_MOCK=1 is set in the environment.
# The mock provides one simulated STM32 DFU device and a deterministic
# flash flow (no real USB hardware required).


class MockUSB:
    MOCK_VID = 0x0483
    MOCK_PID = 0xDF11

    MOCK_EMPTY = False  # set True via PICOLET_PYDFU_MOCK_EMPTY=1

    def list_dfu_devices(self):
        if self.MOCK_EMPTY:
            return []
        return [
            {
                "bus": 1,
                "addr": 1,
                "vid": self.MOCK_VID,
                "pid": self.MOCK_PID,
                "manufacturer": "STMicro",
                "product": "STM32 DFU",
            }
        ]

    def get_memory_layout(self, device_id):
        return [
            {
                "addr": 0x08000000,
                "last_addr": 0x080FFFFF,
                "size": 1048576,
                "num_pages": 256,
                "page_size": 4096,
            }
        ]

    def flash_device(self, device_id, elements, progress_cb):
        """Simulate flashing by calling progress_cb per 2 KiB block."""
        total = sum(e["size"] for e in elements)
        done = 0
        for elem in elements:
            pos = 0
            while pos < elem["size"]:
                chunk = min(2048, elem["size"] - pos)
                done += chunk
                progress_cb(elem["addr"] + pos, done, total)
                pos += chunk

    def abort_flash(self):
        pass
