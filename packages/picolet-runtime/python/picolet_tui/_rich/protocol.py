"""picolet_tui._rich.protocol — Rich's renderable duck-typing helpers.

Ported from Rich master (https://github.com/Textualize/rich,
rich/protocol.py).  Upstream is ~50 LoC of pure duck-typing; no Rich-internal
imports, so this is Tier 1.

Removed vs upstream:
  - `from typing import Any, cast, Set, TYPE_CHECKING` — the typing shim's
    `cast` is identity anyway and the TYPE_CHECKING block only existed to
    appease static checkers.  Dropped to keep the frozen-bytes footprint
    minimal (NFR-TUI-19) and to avoid pulling the typing shim at import time
    for a module that has no runtime use for it.
  - The `_GIBBERISH` sentinel + "object claims to have every attribute"
    guard.  Rich uses it to defeat pathological __getattr__ implementations
    (e.g. mock.Mock); picolet's renderable surface is closed (only widgets
    and built-in Rich primitives reach the console), so the guard is dead
    weight.  Re-add narrowly if a downstream port ever needs it.
  - typing.Protocol / runtime_checkable machinery — synthesis D7 declares
    runtime-checkable Protocol out-of-scope; we duck-type on hasattr
    directly, matching what Rich actually does at the call site.

Supports:
  FR-TUI-13 (console renderables): is_renderable() is the gate Rich's
    Console uses to decide whether to dispatch __rich_console__ vs treat
    the object as a printable.
  FR-TUI-14 (rich-cast chain): rich_cast() implements the __rich__ ->
    __rich__ -> ... unwrap loop that lets widgets return wrapper objects
    (Text, Panel) from their render hooks.
  NFR-TUI-6 (cache budget): the visited-type set caps recursion at the
    number of distinct types in the chain, so a self-referential __rich__
    can't grow the cache unbounded.
"""


def is_renderable(check_object):
    """Check if an object may be rendered by Rich.

    A string, or anything exposing __rich__ / __rich_console__, qualifies.
    Matches upstream Rich's signature exactly so widget code that calls
    `is_renderable(child)` ports unchanged.
    """
    # hasattr() is the only viable check under MicroPython — isinstance
    # against a Protocol class would require runtime_checkable, which is a
    # no-op in our typing shim and so would accept everything.
    return (
        isinstance(check_object, str)
        or hasattr(check_object, "__rich__")
        or hasattr(check_object, "__rich_console__")
    )


def rich_cast(renderable):
    """Cast an object to a renderable by calling __rich__ if present.

    Walks the __rich__ chain until it hits a leaf renderable (something
    without __rich__, or a type object, or a cycle).  Returns the final
    object unchanged if no __rich__ hook is present.

    Args:
        renderable: A potentially renderable object.

    Returns:
        The result of recursively calling __rich__, or the input if no
        __rich__ hook exists.
    """
    # Track types, not instances: two different Text() instances reached
    # via different __rich__ hops are fine, but the same type appearing
    # twice means we're in a loop (Text.__rich__ -> Text, etc.).
    rich_visited_set = set()
    while hasattr(renderable, "__rich__") and not isinstance(renderable, type):
        cast_method = getattr(renderable, "__rich__")
        renderable = cast_method()
        renderable_type = type(renderable)
        if renderable_type in rich_visited_set:
            break
        rich_visited_set.add(renderable_type)

    return renderable
