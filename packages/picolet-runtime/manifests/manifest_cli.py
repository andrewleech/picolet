# Minimal frozen-manifest baseline for the picolet `cli` variant.
#
# Pulls in: asyncio (FR-IPC-5 prerequisite), os-path (used by user code).
# Does NOT pull in json — the unix port has it as a built-in C module.
#
# add_library registrations for python-stdlib and python-ecosys are both
# declared up front: asyncio lives under python-stdlib; python-ecosys is
# pre-registered to avoid editing this file when PH06+ need it.

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

require("asyncio")   # FR-IPC-5: asyncio is the Python-side scheduler.
require("os-path")   # os.path module; consumed by user code routinely.
