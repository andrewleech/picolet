"""picolet_tui._rich.repr - ``@rich.repr.auto`` decorator.

Ported from Textualize/rich master @ 63d3200199f6d4a01268d71f98f27dbe416ee268
(``rich/repr.py``, 150 LoC upstream).  Tier 4 of the Rich subset.
No dependencies on other Rich modules.

REMOVED vs upstream
-------------------
* ``auto_rich_repr`` inspect.signature fallback.  Upstream's ``@auto``
  decorator, when applied to a class that does NOT define
  ``__rich_repr__``, synthesises one by walking ``__init__``'s
  signature and yielding each parameter as ``(name, getattr(self,
  name), default)``.  That path requires ``inspect.signature``, which
  MicroPython does not implement (``_callback.count_parameters`` only
  exposes arity, not parameter names or defaults).

  Per synthesis decision D9 / rich-subset note 5, every Rich and
  Textual class that ships with the library already defines an
  explicit ``__rich_repr__``, so the introspection fallback is dead
  code in practice.  We replace it with a ``NotImplementedError`` so
  callers that relied on it fail loudly instead of silently producing
  the wrong repr.

* ``@overload`` decorators.  MicroPython's ``typing`` shim ignores
  them but they add import surface for no runtime effect.

* ``if __name__ == "__main__"`` demo block (~25 LoC) -- pulled in
  ``rich.console``, which is far outside the Tier 4 surface.

Spec references
---------------
* FR-Rich-Subset (v0.1 spec): keep the ``@rich.repr.auto`` decorator
  on the public surface so Textual widget classes import cleanly.
* NFR-MP-Compat: no ``inspect`` introspection at runtime.
"""

# Tuple shapes (no typing.Union at runtime to keep the shim light):
#   yielded items may be a bare value, ``(value,)``, ``(key, value)``,
#   or ``(key, value, default)``.
Result = object  # alias kept for ``from rich.repr import Result`` users
RichReprResult = Result


class ReprError(Exception):
    """An error occurred when attempting to build a repr."""


def auto(cls=None, *, angular=None):
    """Class decorator to create __repr__ from __rich_repr__."""

    def do_replace(cls, angular=None):
        def auto_repr(self):
            repr_str = []
            append = repr_str.append
            # Upstream stores the flag on the ``__rich_repr__`` method
            # object (``cls.__rich_repr__.angular``), but MicroPython
            # functions reject attribute assignment, so we keep it as a
            # class attribute instead.  Same default (False) and the
            # class-level lookup means subclasses inherit it, matching
            # upstream's method-attribute inheritance behaviour.
            is_angular = getattr(self, "__rich_repr_angular__", False)
            for arg in self.__rich_repr__():
                if isinstance(arg, tuple):
                    if len(arg) == 1:
                        append(repr(arg[0]))
                    else:
                        key = arg[0]
                        value = arg[1]
                        default = arg[2:]
                        if key is None:
                            append(repr(value))
                        else:
                            if default and default[0] == value:
                                continue
                            append("{}={!r}".format(key, value))
                else:
                    append(repr(arg))
            if is_angular:
                return "<{} {}>".format(
                    self.__class__.__name__, " ".join(repr_str)
                )
            return "{}({})".format(
                self.__class__.__name__, ", ".join(repr_str)
            )

        if not hasattr(cls, "__rich_repr__"):
            # Per D9: no inspect.signature fallback.  Force callers to
            # provide an explicit __rich_repr__ rather than silently
            # synthesising a broken one.
            raise NotImplementedError(
                "picolet rich subset requires an explicit __rich_repr__; "
                "the inspect.signature auto-discovery path was dropped "
                "(see research/00-synthesis.md D9)."
            )

        cls.__repr__ = auto_repr
        if angular is not None:
            # Class attribute, not ``cls.__rich_repr__.angular``: function
            # objects on MicroPython raise AttributeError on attribute
            # assignment, which would crash every ``@auto(angular=...)``
            # decoration at import time.
            cls.__rich_repr_angular__ = angular
        return cls

    if cls is None:
        # Bound-args form: ``@auto(angular=True)``.  Return a closure
        # rather than functools.partial -- partial works on MicroPython
        # but the closure is one fewer import.
        def _decorator(target_cls):
            return do_replace(target_cls, angular=angular)

        return _decorator
    return do_replace(cls, angular=angular)


def rich_repr(cls=None, *, angular=False):
    """Alias of :func:`auto` kept for ``@rich.repr.rich_repr`` users."""
    if cls is None:
        return auto(angular=angular)
    return auto(cls)
