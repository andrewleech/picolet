# Lean variant for the picolet webview runtime (linux-x64, PH07).
#
# Forked from picolet-cli/mpconfigvariant.mk.  Delta is the frozen
# manifest pointer; everything else is identical.  libffi is enabled
# (MICROPY_PY_FFI=1) for the WebKitGTK 4.1 bindings frozen under
# packages/picolet-runtime/python/picolet_ui.

# Enable the ffi module and build libffi from source (static link).
MICROPY_PY_FFI = 1
MICROPY_STANDALONE = 1

# Disable SSL — not in the webview baseline.
MICROPY_PY_SSL = 0
MICROPY_SSL_MBEDTLS = 0
MICROPY_SSL_AXTLS = 0

# Frozen manifest: webview variant pulls in both picolet (PH06 dispatcher)
# and picolet_ui (PH07 WebKitGTK bindings).
FROZEN_MANIFEST ?= $(PICOLET_RUNTIME_ROOT)/manifests/manifest_webview_unix.py
