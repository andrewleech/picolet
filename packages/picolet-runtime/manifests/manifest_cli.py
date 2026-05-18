# Frozen-manifest baseline for the picolet `cli` variant.
#
# Pulls in:
#   - asyncio   (FR-IPC-5 prerequisite, used directly by picolet._dispatcher)
#   - os-path   (used by user code)
#   - picolet     (the runtime-side IPC dispatcher package; PH06+)
#
# Does NOT pull in json — the unix and windows ports have it as a
# built-in C module.
#
# asyncio lives in extmod/asyncio (not in micropython-lib) so it is
# included via include() rather than require().  This matches the
# pattern used by the upstream unix 'standard' variant's manifest.py.
#
# os-path lives in micropython-lib/python-stdlib so require() is correct.
#
# The picolet package lives next to this manifest at
# ../python/picolet/.  manifestfile.py chdirs into the manifest's
# directory before exec, so relative paths resolve correctly here.

add_library("python-stdlib", "$(MPY_LIB_DIR)/python-stdlib")
add_library("python-ecosys", "$(MPY_LIB_DIR)/python-ecosys")

# asyncio: from extmod, not from micropython-lib.  The extmod manifest
# packages the right subset (__init__, core, event, funcs, lock, stream).
include("$(MPY_DIR)/extmod/asyncio")

require("os-path")   # os.path module; consumed by user code routinely.
require("pathlib")   # pathlib.Path; used by example apps (notes, config-editor).
require("__future__")  # no-op shim; mpy-cross compiles from __future__ to bytecode.

# picolet IPC dispatcher (PH06+).  freeze() with a base path and a
# script="picolet" walks ../python/picolet/*.py and freezes them as the
# `picolet` package.  Path is relative to this manifest file's directory
# (manifestfile.py chdirs there before exec).
freeze("../python", "picolet")
