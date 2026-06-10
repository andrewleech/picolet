"""picolet_tui._shims — stdlib substitute pack (Phase 2b).

Importing this package on MicroPython registers each shim in
``sys.modules`` under its real stdlib name, so downstream imports
(``import dataclasses``, ``from typing import Any``, ``from weakref
import WeakSet``) resolve without per-callsite rewrites.
``picolet_tui/__init__.py`` imports this package before anything else,
which locks the registration in ahead of every framework module.

Registration is MicroPython-only: on CPython the real stdlib exists and
shadowing it process-wide would poison the host (pytest itself imports
``typing``, ``enum``, ``functools``...).  Framework modules that want a
shim unconditionally import it by explicit path
(``from picolet_tui._shims.typing import ...``) — that works on both
interpreters and is the dominant convention in this tree.

Registration overwrites an existing ``sys.modules`` entry only for
names whose frozen micropython-lib counterpart is a strict subset of
the shim (``functools``: the shim re-exports the frozen ``partial`` /
``reduce`` and adds ``lru_cache`` etc., so the shim must win even if
the frozen module was imported first).

Shims:
  dataclasses    — research doc 03 §"Per-module table"; no frozen/slots
  typing         — every name callable + subscriptable; Protocol as plain class
  enum           — Enum + IntEnum + Flag/IntFlag + @enum_class decorator
  functools      — lru_cache (maxsize=128 default per NFR-TUI-6), wraps,
                   total_ordering, cached_property
  weakref        — WeakSet, WeakValueDictionary, WeakKeyDictionary over weakref.ref
  contextlib     — AsyncExitStack, asynccontextmanager, nullcontext
  selectors      — minimal DefaultSelector surface
  threading      — minimal Lock/Event surface
  callback       — count_parameters() replacing inspect.signature

Budget: NFR-TUI-19 caps the _shims subtree at 20 KiB romfs.
"""

import sys

if sys.implementation.name == "micropython":
    # Import order matters only for functools: its module body does
    # ``from functools import partial, reduce`` against the *frozen*
    # micropython-lib module, which must still be visible in
    # sys.modules at that moment — i.e. before we overwrite the name.
    from . import (
        callback,
        contextlib,
        dataclasses,
        enum,
        functools,
        selectors,
        threading,
        typing,
        weakref,
    )

    for _name, _mod in (
        ("callback", callback),
        ("contextlib", contextlib),
        ("dataclasses", dataclasses),
        ("enum", enum),
        ("functools", functools),
        ("selectors", selectors),
        ("threading", threading),
        ("typing", typing),
        ("weakref", weakref),
    ):
        sys.modules[_name] = _mod
