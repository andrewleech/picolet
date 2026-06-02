# picolet_tui._shims — stdlib substitute pack (Phase 2b).
#
# Each shim ships as a module under this package and registers itself in
# sys.modules under its real stdlib name from inside picolet_tui/__init__.py,
# so downstream imports (`import dataclasses`, `from typing import Any`,
# `from weakref import WeakSet`) resolve without per-callsite rewrites.
#
# Phase 2b agents populate:
#   dataclasses    — research doc 03 §"Per-module table"; no frozen/slots
#   typing         — every name callable + subscriptable; Protocol as plain class
#   enum           — Enum + IntEnum + Flag
#   functools      — lru_cache (maxsize=128 default per NFR-TUI-6), wraps,
#                    total_ordering, cached_property
#   weakref        — WeakSet, WeakValueDictionary, WeakKeyDictionary over weakref.ref
#   contextlib     — AsyncExitStack, asynccontextmanager, nullcontext
#   callback       — count_parameters() replacing inspect.signature
#
# Budget: NFR-TUI-19 caps the _shims subtree at 20 KiB romfs.
