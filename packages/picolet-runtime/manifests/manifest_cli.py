# Minimal frozen-manifest baseline for the picolet `cli` variant.
#
# Pulls in: asyncio (FR-IPC-5 prerequisite), os-path (used by user code).
# Does NOT pull in json — the unix port has it as a built-in C module.
#
# asyncio lives in extmod/asyncio (not in micropython-lib) so it is
# included via include() rather than require().  This matches the pattern
# used by the upstream unix 'standard' variant's manifest.py.
#
# os-path lives in micropython-lib/python-stdlib so require() is correct.

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

# asyncio: from extmod, not from micropython-lib.  The extmod manifest
# packages the right subset (__init__, core, event, funcs, lock, stream).
include("$(MPY_DIR)/extmod/asyncio")

require("os-path")   # os.path module; consumed by user code routinely.
