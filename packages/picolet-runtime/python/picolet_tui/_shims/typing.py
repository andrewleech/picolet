"""picolet_tui._shims.typing — runtime-only stand-in for CPython's typing module.

Implemented:
  TypeVar, ParamSpec, NewType        — name-bearing identity helpers
  Generic, Protocol                  — empty bases; subclassable + subscriptable
  runtime_checkable, overload, cast  — identity / pass-through
  Self, TypeAlias, Final, ClassVar,
    Literal, Annotated, Union,
    Optional, Callable, List, Dict,
    Tuple, Set, FrozenSet, Type,
    Iterator, Iterable, Generator,
    Sequence, Mapping, MutableMapping,
    AsyncIterator                    — _Placeholder singletons; subscript returns self
  TYPE_CHECKING = False
  get_type_hints(obj, ...)           — returns obj.__annotations__ if present, else {}

Deliberately NOT implemented (see docs/tui/research/03-mp-stdlib-coverage.md):
  - True PEP-484 semantics (variance, bounds, constraints) — Textual/Rich never
    introspect them at runtime; the v0.1 spec does not promise type-checker
    fidelity inside the frozen runtime.
  - TypedDict / NamedTuple constructors — out of scope for the Phase 4-5
    widget set; if a downstream port needs them, add narrowly.
  - get_args / get_origin — Textual core doesn't call them; Phase 5 reactive
    code reads annotations as strings via get_type_hints fallback only.

Supports the spec's NFR-TUI-19 (frozen-bytes budget) by keeping every name
backed by the same _Placeholder singleton, and unblocks FR-TUI-19..22
(reactives) + FR-TUI-12 (Message dataclasses) whose import-time `List[X]`
and `Optional[Y]` subscripts would otherwise crash on MicroPython, which
ships no `typing` module at all.
"""


class _Placeholder:
    """Single class behind every subscriptable typing alias.

    Returning self from __class_getitem__ (and from instance __getitem__)
    means `List[int]`, `Dict[str, Foo]`, and `Optional[Bar]` all evaluate
    at import time without allocation — important under NFR-TUI-6's
    cache-budget guidance and keeps the frozen heap quiet."""

    __slots__ = ()

    def __class_getitem__(cls, _item):
        return cls

    def __getitem__(self, _item):
        return self

    def __call__(self, *args, **_kwargs):
        # Mirrors cast()/NewType() shape: when a name is invoked as a
        # function (e.g. `Callable(...)` in defensive user code), hand
        # back the first positional so call sites do not silently lose
        # data.  Returns None for the zero-arg case rather than raising
        # — the shim's contract is "harmless", not "strict".
        return args[0] if args else None

    def __repr__(self):
        return "<picolet_tui typing placeholder>"


_PLACEHOLDER = _Placeholder()


class TypeVar:
    """Name-bearing identity object.

    Textual logs reactive descriptors by TypeVar name in a couple of
    places (e.g. `repr(self._type)`), so we keep `.name` and a useful
    __repr__ even though the constraints/bounds are ignored."""

    __slots__ = ("name", "__constraints__", "__bound__",
                 "__covariant__", "__contravariant__")

    def __init__(self, name, *constraints, bound=None,
                 covariant=False, contravariant=False):
        self.name = name
        self.__constraints__ = constraints
        self.__bound__ = bound
        self.__covariant__ = covariant
        self.__contravariant__ = contravariant

    def __class_getitem__(cls, _item):
        return cls

    def __getitem__(self, _item):
        return self

    def __repr__(self):
        return "~" + self.name


class ParamSpec(TypeVar):
    """PEP-612 placeholder.  Inherits TypeVar so .args / .kwargs lookups
    fall through to _Placeholder via __getattr__-less identity."""

    def __repr__(self):
        return "**" + self.name


class Generic:
    """Empty base allowing `class Foo(Generic[T]):` to evaluate.

    The metaclass-free form is intentional: MicroPython's class machinery
    handles __class_getitem__ on the base directly, so we avoid the
    cost (and the freezer-time complexity) of a custom metaclass."""

    def __class_getitem__(cls, _item):
        return cls


# Protocol is structurally identical to Generic for runtime purposes;
# the type-checker meaning is irrelevant inside the frozen build.
Protocol = Generic


def runtime_checkable(cls):
    return cls


def overload(fn):
    return fn


def cast(_typ, val):
    return val


def NewType(name, _tp):
    """Return a callable that passes its argument through unchanged.

    Matches CPython's runtime behaviour (NewType is identity at runtime;
    only static checkers see the distinction)."""
    def _identity(x):
        return x
    _identity.__name__ = name
    return _identity


def get_type_hints(obj, _globalns=None, _localns=None, include_extras=False):
    # Textual's reactive system calls this to discover watcher annotations;
    # falling back to __annotations__ keeps the descriptor wiring honest
    # without pulling in the full eval-string-annotations machinery.
    del include_extras
    return getattr(obj, "__annotations__", {}) or {}


# Every remaining typing name resolves to the same _Placeholder singleton.
# Listed explicitly (rather than via __getattr__) so the freezer can see
# them as module attributes and so `from typing import X` raises ImportError
# loudly for anything we have not yet covered.
Self = TypeVar("Self")
TypeAlias = _PLACEHOLDER
Final = _PLACEHOLDER
ClassVar = _PLACEHOLDER
Literal = _PLACEHOLDER
Annotated = _PLACEHOLDER
Union = _PLACEHOLDER
Optional = _PLACEHOLDER
Callable = _PLACEHOLDER
List = _PLACEHOLDER
Dict = _PLACEHOLDER
Tuple = _PLACEHOLDER
Set = _PLACEHOLDER
FrozenSet = _PLACEHOLDER
Type = _PLACEHOLDER
Iterator = _PLACEHOLDER
Iterable = _PLACEHOLDER
Generator = _PLACEHOLDER
Sequence = _PLACEHOLDER
Mapping = _PLACEHOLDER
MutableMapping = _PLACEHOLDER
AsyncIterator = _PLACEHOLDER
Any = _PLACEHOLDER

TYPE_CHECKING = False
