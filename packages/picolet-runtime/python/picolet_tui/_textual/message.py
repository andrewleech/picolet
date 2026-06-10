"""picolet_tui._textual.message - Message base class.

Implements the FR-TUI-12 Message type that all framework and user events
derive from.  Bubbling and dispatch live in ``message_pump`` (sibling
module) - keeping Message dependency-free preserves the import ordering
the synthesis pins down: Reactive / Message / @widget are leaf modules,
MessagePump pulls Message in, Widget pulls MessagePump in, and so on.

Spec coverage:
  * FR-TUI-12 - bubble flag and ``stop()`` semantics ("a handler may call
    ``message.stop()`` to halt bubbling").
  * FR-TUI-13 - the ``@on(MessageType, selector=...)`` handler decorator
    is colocated here because it carries the ``_tui_on`` marker that the
    @widget decorator's bucket-3 scan picks up.  Selector parsing itself
    is owned by Phase 4b agent 5 (Binding/selectors) - the decorator just
    stashes a ``_Selector`` value tuple per call.

Design-doc references (textual-core-design.md):
  * §3.1 - Message base shape.
  * §3.4 - bubbling stops when ``_stop_bubble`` is True; the pump
    inspects the flag after every handler invocation.

Subclass introspection note (design §3.1, §1.1 bucket 3):
  Upstream Textual uses ``Message.__init_subclass__`` to register message
  classes with their namespace, the @on selector framework, and bubble
  flags.  MicroPython does not invoke ``__init_subclass__`` (synthesis
  D1).  The picolet replacement is: any user-defined Message subclass
  that needs framework-side wiring (i.e. carries @on handlers or watcher
  hooks) must itself be decorated with @widget.  Plain message subclasses
  that only carry data attributes are valid as plain ``class M(Message):``
  declarations and do not need @widget - the @widget decorator scans the
  *handler-owning* class (the widget receiving the message), not the
  message class itself.  This is what the §1.1 bucket-3 walk consumes.
"""

# No imports from message_pump - by design, see module docstring.


# Module-level constants live here so subclasses can opt out at the class
# level (``class Tick(Message): bubble = False``) without subclassing a
# whole alternative base.  Upstream Textual exposes ``bubble`` as a class
# attribute for exactly this reason; we follow the same convention.


class Message:
    """Base class for all events dispatched through MessagePump.

    Subclasses carry payload attributes; the framework only requires that
    ``Message.__init__`` runs so the bubble bookkeeping is populated.

    Class attributes the framework consults:
      * ``bubble`` - True by default; if False, the pump dispatches only
        on the originating node and never walks the parent chain.  See
        §3.4 in the design doc; the check is one ``getattr`` per message.
      * ``namespace`` - reserved for v0.2 selector matching (CSS-like
        ``#id`` and ``.class`` selectors per spec §3.6).  Present as an
        attribute so downstream agents can read it without an
        ``AttributeError``; the v0.1 selector parser ignores it.
    """

    # Default bubble behaviour - the synthesis takes the upstream Textual
    # default verbatim (FR-TUI-12: "a handler may call ``message.stop()``
    # to halt bubbling" - bubbling is the default that ``stop()`` halts).
    bubble = True

    # v0.2 reservation; see class docstring.
    namespace = None

    def __init__(self):
        # _stop_bubble is the in-flight flag the dispatch loop checks
        # after every handler.  Per-instance because two listeners on
        # the same Message type would otherwise race.  Underscore prefix
        # because user widget code reads via ``message.stop()`` /
        # ``message._stop_bubble``; ``stop_propagation()`` is the
        # explicit-named alias documented in the FR-TUI-12 / D6 trail.
        self._stop_bubble = False
        # _sender is set by MessagePump.post_message when a widget posts
        # this message; it is None for messages constructed by user code
        # outside a widget context.  Read by selectors at dispatch time
        # for the ``#id`` and ``.class`` forms (Phase 4b agent 5).
        self._sender = None
        # _handler_args is reserved: upstream Textual stores the bound
        # handler argument list here so the same Message can be redispatched
        # with the previously-resolved arity.  v0.1 always resolves arity
        # at dispatch time (cheap; see message_pump._dispatch); the slot
        # is kept so v0.2 caching does not need a Message ABI bump.
        self._handler_args = None

    def stop(self):
        """Halt bubbling immediately.

        Called by user handler code.  Equivalent to setting
        ``_stop_bubble = True`` directly; the alias preserves the
        upstream Textual ``message.stop()`` ergonomic so migration
        across the table in §10 of the design doc is a no-op for this
        call site.
        """
        self._stop_bubble = True

    def stop_propagation(self):
        """Explicit-named alias for ``stop()``.

        Some upstream-Textual code (and most DOM-event-flavoured
        documentation) reads better with the long form.  The two names
        do not diverge - one sets the same flag the other does.
        """
        self._stop_bubble = True

    def prevent_default(self):
        """Reserved for v0.2 default-action suppression.

        The upstream Textual signature exists for symmetry with the DOM
        event model; v0.1 has no default-action surface to suppress, so
        the call is a no-op.  Present so migration code that calls it
        does not crash - the alternative (``AttributeError``) is the
        sharpest possible regression for a user porting from upstream.
        """
        return None

    def can_replace(self, other):
        """Coalescing predicate for queue compaction.

        Called by MessagePump when an incoming message would otherwise
        be dropped at the 4096-cap overflow boundary (design §3.5).  A
        Message subclass that represents an idempotent state delta -
        for example a resize or a paint refresh - may return True to
        let the pump drop ``other`` from the queue and append ``self``
        in its place, collapsing redundant updates into one.

        Default: False.  Conservatively, no coalescing.  Phase 5
        widgets that ship coalescing-eligible messages (``Resize``,
        ``Refresh``) override this method to compare by type.

        The signature matches upstream Textual; the implementation is
        intentionally minimal because v0.1's queue cap is high enough
        that the steady-state cost of *not* coalescing is one extra
        deque popleft per overflow event - negligible against the cost
        of a per-message Python-level type comparison.
        """
        return False

    def __repr__(self):
        # __repr__ is here (not in @widget-synthesised __repr__) because
        # Message subclasses are not @widget-decorated by default; see
        # the migration table in §10.  Identity-style repr is enough -
        # message instances are short-lived and the dispatch path does
        # not need a stable string form.
        return "<%s>" % type(self).__name__


# -----------------------------------------------------------------------
# @on decorator — re-exported from _widget_decorator.
# -----------------------------------------------------------------------
#
# This module previously carried its own ``on`` implementation (and a
# placeholder ``_Selector`` whose ``matches()`` always returned True).
# That duplicated _widget_decorator's complete implementation — a
# Phase 4b parallel-agent artifact — and meant handlers registered
# through the package facade silently skipped ``#id`` selector
# filtering.  One implementation now lives in _widget_decorator (which
# imports nothing from this module, so there is no cycle); this
# re-export keeps ``from picolet_tui._textual.message import on``
# working.

from ._widget_decorator import on  # noqa: F401  - canonical implementation
