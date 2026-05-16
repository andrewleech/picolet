# picolet._errors — exception preservation across the IPC wire (FR-IPC-2).
#
# JSON cannot carry Python class identity, so the dispatcher transmits an
# {"type": <str>, "message": <str>} pair and reconstructs an exception
# locally at the receiving end:
#
#   - If "type" matches a known builtin exception name, the corresponding
#     builtin class is raised with the original message.  This is what
#     lets `try: await picolet.invoke(...) except ValueError as e: ...` work
#     across the wire when the peer raised ValueError.
#
#   - Otherwise, RemoteError is raised carrying the type name as a string
#     attribute.  The dispatcher never reaches into globals() to resolve
#     arbitrary type names — that would let a malicious peer pick any
#     callable in the receiver's namespace.

import builtins


class RemoteError(Exception):
    """Raised by ``picolet.invoke`` when the peer returned an error whose
    ``type`` is not a known builtin exception class.

    Attributes:
        type_name: the type name the peer reported (a string, never a class).
        message: the message string the peer reported.
    """

    def __init__(self, message, type_name=None):
        super().__init__(message)
        self.type_name = type_name or "RemoteError"
        self.message = message

    def __str__(self):
        if self.type_name and self.type_name != "RemoteError":
            return "{}: {}".format(self.type_name, self.message)
        return self.message


# Allow-list of builtin exception type names that the receiver is willing
# to reconstruct as the matching builtin class.  Anything outside this set
# becomes a RemoteError.  Keep this list conservative — every name added
# here is a callable that any peer can cause the local process to
# instantiate.
#
# Security rationale (intentional BaseException exclusion):
#
#   This list contains only ``Exception`` subclasses.  ``KeyboardInterrupt``
#   and ``SystemExit`` (and any other direct ``BaseException`` subclass)
#   are deliberately absent and must stay absent.  Code in the host
#   process — and most user ``except`` clauses — catches ``Exception``,
#   not ``BaseException``; ``BaseException`` subclasses propagate through
#   normal cleanup paths and either trip ``sys.excepthook``
#   (``KeyboardInterrupt``) or unwind the interpreter (``SystemExit``).
#   Allowing a peer to specify ``"type": "KeyboardInterrupt"`` would let
#   a malicious or buggy peer crash the host or skip user-level cleanup
#   handlers without writing any code that catches the exception.  The
#   safe behaviour is to surface unknown / disallowed type names as
#   ``RemoteError`` (an ``Exception`` subclass), which user code can
#   handle the same as any other remote failure.
_BUILTIN_EXCEPTION_NAMES = (
    "Exception",
    "RuntimeError",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "AssertionError",
    "ArithmeticError",
    "ZeroDivisionError",
    "OverflowError",
    "LookupError",
    "NameError",
    "NotImplementedError",
    "OSError",
    "StopIteration",
    "MemoryError",
    "FileNotFoundError",
    "EOFError",
    "ImportError",
)


def build_exception(error):
    """Build a Python exception from a wire-format error dict.

    The wire shape is::

        {"type": "ValueError", "message": "bad input"}

    Missing fields default to ``"Exception"`` / ``""``.  Returns the
    exception **instance** (caller raises it).
    """
    if not isinstance(error, dict):
        return RemoteError("malformed error payload from peer", "RemoteError")
    name = error.get("type", "Exception")
    msg = error.get("message", "")
    if not isinstance(name, str):
        name = "RemoteError"
    if not isinstance(msg, str):
        msg = str(msg)
    if name in _BUILTIN_EXCEPTION_NAMES:
        cls = getattr(builtins, name, None)
        if cls is not None:
            return cls(msg)
    return RemoteError(msg, name)


def error_payload(exc):
    """Serialise a Python exception to the wire-format error dict.

    Stack traces, ``__cause__``, and ``__context__`` are *not* transmitted
    by design — the contract is type-name + message only.
    """
    return {
        "type": type(exc).__name__,
        "message": str(exc),
    }
