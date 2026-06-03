"""picolet_tui.widgets.input - Input widget (FR-TUI-47..49).

A single-line text-entry widget.  Tracks a string ``value``, a caret
``cursor_position`` (character offset into ``value``), and a couple of
display-only Reactives (``placeholder``, ``password``).  Bindings cover
the v0.1 edit set: enter / backspace / delete / arrows / home / end.
The ``on_key`` fallback handles every other printable character.

Spec coverage:
  * FR-TUI-47 - constructor signature
    ``Input(value="", *, placeholder="", password=False,
            max_length=None)``; submit/enter emits
    ``Input.Submitted(value=self.value)``; every change emits
    ``Input.Changed(value=self.value)``.
  * FR-TUI-48 - Paste handling: ``events.Paste`` inserts the payload
    at the caret in one edit step, truncating at ``max_length`` and
    stripping unprintable control bytes (< 0x20, except ``\\t``).
  * FR-TUI-49 - password mode renders each character as U+2022 but
    the real value still appears on Submitted.
  * FR-TUI-52 - accepts ``id`` / ``classes`` constructor kwargs.

Design-doc references (textual-core-design.md):
  * \xa74.6 - "Input (5): ``value`` reactive, edit-key bindings,
    ``Paste`` handler".  This file is the v0.1 realisation.
  * \xa76.1/6.3 - BINDINGS list shape + the action_<name> dispatch
    convention.  Every BINDINGS entry here points at an
    ``action_*`` method on this class.
  * \xa77.1 - render() returns one of {str, Text, __rich_console__}.

Design decisions that deviate from the prompt:

  * The prompt sketch lists Submitted / Changed as inner classes.  We
    keep them as inner classes (matches upstream Textual idiom) but
    do NOT decorate them with ``@widget`` - they are plain
    data-carrying ``Message`` subclasses.  The @widget decorator is
    for handler-owning classes (receivers).  Per the
    ``_key_dispatch.py`` comment at line 76-79, plain Message
    subclasses skip the decorator.

  * ``max_length`` is part of the FR-TUI-47 signature but absent from
    the prompt sketch.  We accept and honour it - dropping it would
    fail the test in
    ``tests/widgets/test_input.py::test_paste_truncate_strip``.

  * We render via a Rich ``Text`` instance so the cursor cell can be
    styled distinctly (``reverse`` SGR) on a single character.  The
    plain-string return shape would not let us mark one cell while
    leaving the rest unstyled.

  * ``cursor_position`` is a Reactive so external code (a parent
    widget showing a cursor coordinate, say) can ``watch_`` it.  Per
    FR-TUI-19 the reactive layer is the documented external mutation
    surface; we use it ourselves for the same reason.

  * The watchers on ``value`` and ``cursor_position`` clamp the caret
    into ``[0, len(value)]``.  This is the same defensive idiom Static
    uses for ``_renderable``: keep the cached state consistent
    regardless of which surface the user wrote to.

  * Bindings use the canonical ``Keys.*`` strings (``"backspace"``,
    ``"delete"``, ``"left"`` etc.) which is what the parser in
    ``_key_dispatch.py`` emits.  The dispatcher in \xa76.3 matches via
    ``binding.key == key_event.key`` first, then alias lookup; both
    forms land on these actions.

  * We do NOT implement v0.1 selection - the prompt mentions
    "cursor + selection" in the docstring but the FR-TUI-47 list does
    not enumerate selection keys.  Selection is a v0.2 widget feature
    (synthesis D3 v0.1 widget set excludes it).  The docstring text
    is kept aspirational; the code is single-caret.

Intra-widget ambiguities resolved:

  * Where the prompt sketch shows ``Submitted(self.value)`` we keep
    the kwarg form ``Submitted(value=self.value)`` to match FR-TUI-47
    verbatim.  The inner-class __init__ accepts a single positional
    ``value`` so either form works at the call site; the test names
    in the spec table use ``message.value``, which our attribute
    matches.

  * The prompt's BINDINGS list maps ``"enter"`` -> ``submit``.  The
    parser emits ``Keys.Enter`` == ``"enter"`` for Ctrl-M / CR, and
    aliases ``"ctrl+m"`` via ``KEY_ALIASES``; both routes converge on
    the same Binding.

  * Printable-key insertion happens in ``on_key``, NOT through a
    catch-all binding.  Bindings drive the explicit edit keys
    (enter, backspace, ...) and we let the name-based fallback
    consume the rest.  Returning True from ``on_key`` stops
    propagation when we consumed the byte (so a parent screen does
    not see "a" as a binding key); returning False otherwise.

  * The Paste handler is named ``on_paste`` to match the
    camel_to_snake convention from message_pump.py:482 - dispatching
    a Paste message will look up ``on_paste``.
"""

# Widget base brings DOMNode topology, MessagePump, R3 guard,
# can_focus default, refresh stub.  Subclassing it is mandatory for
# this widget to participate in the focus/binding/dispatch machinery.
from .._textual.widget import Widget

# Reactive descriptor host - the @widget decorator on this class
# discovers each Reactive via vars() walk and binds the name (FR-TUI-19).
# Four reactives: ``value`` (text content), ``cursor_position``
# (caret), ``placeholder`` (display fallback), ``password`` (mask).
from .._textual.reactive import Reactive

# @widget is mandatory on any class declaring Reactives, BINDINGS,
# or on_<event> handlers; Widget.__init__ raises
# MissingWidgetDecoratorError without it (FR-TUI-28 / R3).
from .._textual._widget_decorator import widget

# Binding value type used in the BINDINGS class attribute.  Each
# entry maps a key string to an ``action_<name>`` method on this
# class (\xa76.1/6.3); the dispatcher walks them at key-event time.
from .._textual.binding import Binding

# Message is the base for the Submitted / Changed inner classes.
# Direct import (rather than via ``messages`` aggregate) keeps the
# import graph flat: Input only depends on the leaf Message module.
from .._textual.message import Message

# Rich Text for the render() output.  Used to highlight the cursor
# cell with a ``reverse`` style without affecting the surrounding
# characters.  Imported lazily inside render() would save the import
# cost for headless tests, but render() is on the hot path and the
# Text class is already pulled in by most other widgets - the lazy
# branch is not worth the readability cost here.
from .._rich.text import Text


# ---------------------------------------------------------------------
# _strip_unprintable - paste-payload sanitiser (FR-TUI-48).
#
# Strips control bytes < 0x20 except tab (0x09).  Defined at module
# scope rather than as a static method so it can be tested in
# isolation without instantiating an Input.  Returns a new string;
# the caller takes ownership.
# ---------------------------------------------------------------------


def _strip_unprintable(text):
    """Remove control bytes (< 0x20) from ``text`` except tab.

    Per FR-TUI-48, paste payloads are scrubbed of unrenderable control
    bytes before insertion.  Newlines (0x0A / 0x0D) ARE scrubbed: an
    Input is single-line, and inserting a newline would visually
    corrupt the row.  Tab survives because user code may legitimately
    paste tab-delimited data into a search field.
    """
    # ``join`` over a generator is the cheapest single-pass filter
    # available in MicroPython - no regex compile, no list copy.
    # The filter expression is unrolled inline because the function
    # call cost dominates the comparison cost for the < 0x20 check.
    return "".join(
        ch for ch in text
        if ord(ch) >= 0x20 or ch == "\t"
    )


# ---------------------------------------------------------------------
# Input.
# ---------------------------------------------------------------------


@widget
class Input(Widget):
    """Single-line text entry with a movable caret.

    Construction::

        Input(value="", *, placeholder="", password=False,
              max_length=None, id=None, classes="")

    The widget is focusable (``can_focus = True``).  On focus, key
    events route through the BINDINGS table first; unbound printable
    keys flow into ``on_key`` and are appended at the caret.

    Bound keys (FR-TUI-47):
      * ``enter``      -> post ``Submitted(value=self.value)``
      * ``backspace``  -> delete char left of caret
      * ``delete``     -> delete char at caret
      * ``left`` / ``right``      -> move caret one cell
      * ``home`` / ``end``        -> move caret to start / end
      * ``ctrl+a``     -> home (alias)
      * ``ctrl+e``     -> end (alias)
      * ``ctrl+u``     -> clear value (FR-TUI-47)

    Every assignment to ``value`` fires ``Changed(value=self.value)``;
    the caller may listen via ``@on(Input.Changed)`` or
    ``on_input_changed(self, message)``.

    Password mode (FR-TUI-49): every visible character becomes
    U+2022 ('•').  ``Submitted.value`` carries the real text.

    Paste handling (FR-TUI-48): an ``events.Paste`` event delivered
    to a focused Input inserts the payload at the caret in one edit
    step, truncating at ``max_length`` and stripping unrenderable
    control bytes.
    """

    # ------------------------------------------------------------------
    # Inner message classes.
    # ------------------------------------------------------------------

    # Plain ``Message`` subclasses - no @widget needed because they
    # carry no handlers / reactives / bindings (_key_dispatch.py:76-79).
    # The single ``value`` attribute is what the FR-TUI-47 spec table
    # asserts against in tests/widgets/test_input.py.

    class Submitted(Message):
        """Posted when the user presses ``enter`` (FR-TUI-47).

        The ``value`` attribute is the Input's value at the moment
        enter was pressed - a snapshot, not a live reference.  Callers
        that listen via ``on_input_submitted`` see the value the user
        actually confirmed even if subsequent keystrokes mutate the
        widget before the handler runs.
        """

        def __init__(self, value):
            # Message.__init__ populates _stop_bubble / _sender; skipping
            # it would AttributeError on the first dispatch walk.
            Message.__init__(self)
            self.value = value

    class Changed(Message):
        """Posted on every edit (FR-TUI-47).

        Fires from the ``watch_value`` handler so every mutation path
        (printable keys, backspace, delete, paste, ctrl+u, direct
        ``input.value = ...`` assignment) converges on one emit site.
        The ``value`` attribute is the post-edit value.
        """

        def __init__(self, value):
            Message.__init__(self)
            self.value = value

    # ------------------------------------------------------------------
    # Class attributes.
    # ------------------------------------------------------------------

    # BINDINGS: one entry per FR-TUI-47 edit key.  Each ``.action`` is
    # the bare name; the dispatcher prepends ``action_`` per \xa76.3.
    # Description strings populate the footer widget (v0.2 TCSS); for
    # v0.1 they double as inline documentation of intent.
    BINDINGS = [
        Binding("enter", "submit", "submit"),
        Binding("backspace", "delete_left", "delete previous char"),
        Binding("delete", "delete_right", "delete next char"),
        Binding("left", "cursor_left", "move cursor left"),
        Binding("right", "cursor_right", "move cursor right"),
        Binding("home", "cursor_home", "move cursor to start"),
        Binding("end", "cursor_end", "move cursor to end"),
        # FR-TUI-47 "accepted aliases" set: ctrl+a == home, ctrl+e == end,
        # ctrl+u clears.  Listed AFTER home/end so the canonical bindings
        # are the ones the footer widget displays; the alias rows have
        # show=False to keep the footer uncluttered.
        Binding("ctrl+a", "cursor_home", "", show=False),
        Binding("ctrl+e", "cursor_end", "", show=False),
        Binding("ctrl+u", "clear", "clear"),
    ]

    # The four Reactives.  Defaults match the FR-TUI-47 constructor
    # defaults so an undecorated instance read (before __init__ stores)
    # returns the right shape.
    #
    # ``value`` has a watcher (``watch_value``) that emits Changed and
    # clamps the caret; the watch is the single emit point per the
    # docstring rationale on Changed.
    value = Reactive("")
    # ``cursor_position`` is the offset into the *plain* value string.
    # Password mode does not affect the offset - it only changes what
    # render() displays for each character.
    cursor_position = Reactive(0)
    # ``placeholder`` shows when ``value`` is empty.  Not part of the
    # Submitted/Changed contract - placeholder display is purely a
    # render concern.
    placeholder = Reactive("")
    # ``password`` is a display-only toggle.  Toggling at runtime is
    # supported (the watch_ side simply refreshes) so a "show password"
    # button can flip the visibility live.
    password = Reactive(False)

    # ------------------------------------------------------------------
    # __init__.
    # ------------------------------------------------------------------

    def __init__(
        self,
        value="",
        *,
        placeholder="",
        password=False,
        max_length=None,
        id=None,
        classes="",
    ):
        # Widget.__init__ runs the R3 guard, DOMNode topology, message
        # pump bootstrap.  Forwards FR-TUI-52 kwargs.
        Widget.__init__(self, id=id, classes=classes)

        # An Input is focusable - that is the whole point of the
        # widget.  ``can_focus`` is class-level on Widget but we
        # set it on the instance so user code that adapts the class
        # (subclassing for a read-only Input variant) can flip back.
        # Per the Widget docstring at line 165-169, can_focus is
        # documented as per-class but instance overrides are legal.
        self.can_focus = True

        # ``max_length`` is not a Reactive - the value is rarely
        # mutated post-construction and routing it through the
        # descriptor would impose per-write cost on edit hot paths
        # (every printable key checks max_length).  Stash on a
        # private slot; the value is consulted by ``_insert_text``
        # and ``on_paste``.
        self._max_length = max_length

        # Seed the value first.  We want the caret position to land
        # at the end of the initial value (the upstream Textual
        # convention - users type into a prefilled Input expecting
        # to append).  Setting ``cursor_position`` AFTER ``value`` is
        # important: watch_value clamps the caret, so writing value
        # first and then jumping the caret to end keeps both
        # reactives consistent.
        #
        # The ``value`` set fires watch_value which posts a Changed
        # message.  This is fine at construction time - the pump is
        # not running yet, so the message lands in the queue and
        # drains after mount.  Tests that assert "no Changed on
        # initial construction" use ``Input()`` with the empty
        # default; the Reactive's equality fast-path skips
        # watch_value when the new value equals the default.
        self.value = value
        self.cursor_position = len(value)

        self.placeholder = placeholder
        self.password = password

    # ------------------------------------------------------------------
    # Reactive watchers - the single mutation pipeline.
    # ------------------------------------------------------------------

    def watch_value(self, old, new):
        """Clamp the caret and emit ``Changed`` (FR-TUI-47).

        The single emit site for ``Input.Changed`` - every mutation
        path (printable keys, backspace, delete, paste, ctrl+u, direct
        assignment from user code) writes through ``self.value = ...``
        and ends up here.  Centralising the emit means tests can
        assert "exactly one Changed per edit step" without auditing
        every action_ method.

        Clamps ``cursor_position`` into ``[0, len(new)]``.  A direct
        assignment that *shortens* the value below the current caret
        position would otherwise leave the caret pointing past
        end-of-string, which render() would have to special-case.
        Doing the clamp here keeps the invariant centralised.
        """
        # Length clamp.  cursor_position is itself a Reactive; the
        # assignment fires watch_cursor_position which is a no-op when
        # the value already satisfies the clamp.
        new_len = len(new)
        if self.cursor_position > new_len:
            self.cursor_position = new_len

        # Emit Changed.  post_message routes through the MessagePump
        # which handles bubble and dispatch.  We post even when the
        # value did not actually change (Reactive __set__ already
        # filtered the no-op case via __eq__ before calling us), so
        # arriving in this method is itself the change signal.
        self.post_message(self.Changed(value=new))

    def watch_cursor_position(self, old, new):
        """Clamp the caret to ``[0, len(value)]``.

        External writers (parent widgets, programmatic focus
        restoration) may set ``cursor_position`` directly; the
        clamp here keeps the invariant.  Refresh is implicit -
        Reactive's __set__ calls refresh() after the watcher returns.
        """
        # Length-of-value is the natural upper bound; the caret may
        # sit one cell past the last character (cursor at end-of-line),
        # which is why we use ``len(value)`` not ``len(value) - 1``.
        max_pos = len(self.value)
        if new < 0:
            self.cursor_position = 0
        elif new > max_pos:
            self.cursor_position = max_pos

    # ------------------------------------------------------------------
    # action_* methods - the binding targets.
    # ------------------------------------------------------------------

    def action_submit(self):
        """Post ``Submitted(value=self.value)`` (enter key)."""
        # Submission does NOT clear the value - the user may want to
        # press enter on the same query twice.  This matches upstream
        # Textual; tests asserting "value persists after submit" would
        # fail if we cleared.
        self.post_message(self.Submitted(value=self.value))

    def action_delete_left(self):
        """Delete the character at ``cursor_position - 1`` (backspace).

        No-op at start-of-line (caret == 0).  After the delete the
        caret moves left by one.

        Write order matters: ``self.value`` is written FIRST so the
        watch_cursor_position clamp (against the new shorter value)
        accepts our subsequent caret write.  If we set the caret
        first against the old longer value, the clamp would still
        accept it - but if we then shortened the value, watch_value
        would re-clamp the caret in a way that crosses our intended
        position.  Doing value-then-caret is the only ordering that
        works for both shrink and grow paths.
        """
        pos = self.cursor_position
        if pos == 0:
            return
        v = self.value
        # Splice out the character left of the caret.  String slicing
        # is the canonical idiom in MicroPython - no StringIO buffer,
        # no list-of-chars dance, because the strings are short
        # (single-line input) and slicing is C-level.
        new_value = v[:pos - 1] + v[pos:]
        # Value first - shrinks the buffer, watch_value clamps caret
        # (which is still at the old position pos) to min(pos, new_len)
        # == pos - 1 in the case of a single-char delete since we
        # removed exactly one character before the caret.  Then the
        # explicit caret write below is a no-op confirmation.
        self.value = new_value
        self.cursor_position = pos - 1

    def action_delete_right(self):
        """Delete the character at ``cursor_position`` (delete key).

        No-op at end-of-line (caret == len(value)).  The caret does
        not move.
        """
        pos = self.cursor_position
        v = self.value
        if pos >= len(v):
            return
        self.value = v[:pos] + v[pos + 1:]

    def action_cursor_left(self):
        """Move the caret one cell left.  No-op at start."""
        if self.cursor_position > 0:
            self.cursor_position = self.cursor_position - 1

    def action_cursor_right(self):
        """Move the caret one cell right.  No-op at end."""
        if self.cursor_position < len(self.value):
            self.cursor_position = self.cursor_position + 1

    def action_cursor_home(self):
        """Move the caret to start-of-line (home / ctrl+a)."""
        # Assigning to a Reactive always fires the watcher and a
        # refresh, even when the new value equals the old (Reactive's
        # equality short-circuit skips both).  The guard here avoids
        # the noop refresh; cheap optimisation but matches upstream.
        if self.cursor_position != 0:
            self.cursor_position = 0

    def action_cursor_end(self):
        """Move the caret to end-of-line (end / ctrl+e)."""
        end = len(self.value)
        if self.cursor_position != end:
            self.cursor_position = end

    def action_clear(self):
        """Clear the value entirely (ctrl+u, FR-TUI-47).

        Both ``value`` and ``cursor_position`` are reset.  The order
        matters: setting value first triggers watch_value which clamps
        cursor_position to 0 anyway, so the explicit assignment is
        redundant.  We keep it explicit because the implicit clamp
        is a side effect of the watcher and a reader of this method
        should not have to chase the dependency.
        """
        if self.value != "":
            self.value = ""
        # Belt-and-braces: watch_value clamped the caret already, but
        # if the value was empty to start with the watcher did not
        # fire, leaving a stale caret position.  Explicit reset
        # covers the value-already-empty case.
        if self.cursor_position != 0:
            self.cursor_position = 0

    # ------------------------------------------------------------------
    # on_key - printable-character insertion (FR-TUI-47).
    # ------------------------------------------------------------------

    def on_key(self, event):
        """Insert printable characters at the caret.

        Bindings have already consumed enter / backspace / arrows /
        ctrl+a/e/u by the time this handler fires (per \xa76.3 the
        binding walk runs before name-based dispatch).  We only see
        printable keys plus any control keys we did not bind.

        A printable key is one with a non-None ``event.character`` of
        length 1 and ordinal >= 0x20.  This matches the parser's
        contract in ``_key_dispatch.py``: control bytes have
        ``character = None`` and printable bytes carry their literal
        character.

        Returns True on insertion (so the message stops bubbling - a
        parent screen should not also see the keypress); returns
        nothing for unhandled keys so the message continues to bubble.
        """
        ch = getattr(event, "character", None)
        if ch is None or len(ch) != 1:
            # Not a printable character - let the message bubble.  This
            # is how keys like F1 or unbound ctrl combinations reach
            # parent widgets.
            return
        # ord() guard: defensive double-check for control bytes that
        # somehow got a non-None character set (e.g. \t == 0x09).
        # FR-TUI-48 paste path strips < 0x20 already; on_key applies
        # the same rule to per-key insertions for consistency.
        if ord(ch) < 0x20:
            return

        self._insert_text(ch)
        # Stop bubbling - we consumed the key.
        return True

    # ------------------------------------------------------------------
    # on_paste - bracketed-paste handler (FR-TUI-48).
    # ------------------------------------------------------------------

    def on_paste(self, event):
        """Insert a paste payload at the caret in one edit step.

        FR-TUI-48 contract:
          * Insertion happens in a SINGLE edit step - one Changed
            event fires regardless of payload length.  This falls
            out naturally because we set ``self.value`` once after
            assembling the new string.
          * The payload is truncated at ``max_length`` (if set)
            BEFORE insertion - measured against the assembled
            new-value length, not the bare payload length, because
            a partial paste that still overflows the limit must
            stop short.
          * Unrenderable control bytes are stripped: < 0x20 except
            tab (handled by ``_strip_unprintable``).
        """
        payload = _strip_unprintable(event.text)
        if not payload:
            # Empty payload after stripping - nothing to do.  Skip the
            # value write so watch_value does not fire a no-op Changed.
            return True

        self._insert_text(payload)
        return True

    # ------------------------------------------------------------------
    # _insert_text - the shared insertion primitive.
    # ------------------------------------------------------------------

    def _insert_text(self, text):
        """Splice ``text`` in at the caret, honouring ``max_length``.

        Shared by ``on_key`` (single character) and ``on_paste``
        (arbitrary string).  Centralising the splice ensures both
        paths agree on the max_length check and the caret-advance
        rule.
        """
        pos = self.cursor_position
        v = self.value
        new_value = v[:pos] + text + v[pos:]

        # max_length is measured against the assembled new-value
        # length.  Truncating ``text`` before splice would lose
        # already-typed characters past the caret; truncating after
        # splice retains them and drops the tail of the new value.
        # Upstream Textual's Input does the latter; we match.
        if self._max_length is not None and len(new_value) > self._max_length:
            new_value = new_value[:self._max_length]

        # The caret advances by however many characters were actually
        # inserted (post-truncation).  Computed against the truncated
        # new-value length so a maxed-out Input has the caret pinned
        # at end-of-line rather than past it.
        inserted = len(new_value) - len(v)
        if inserted <= 0:
            # No characters made it past the cap - bail without
            # writing, otherwise watch_value would fire a no-op
            # Changed.  ``inserted < 0`` is impossible (we only ever
            # add to v), kept for defence in depth.
            return
        # Write order: value first, then caret.  The opposite order
        # would leave the caret pointing past the (still-old, shorter)
        # value briefly - watch_cursor_position would clamp the new
        # caret down to len(old_value) before watch_value got a
        # chance to grow the buffer.  See action_delete_left for the
        # symmetric reasoning on shrink.
        self.value = new_value
        self.cursor_position = pos + inserted

    # ------------------------------------------------------------------
    # render() - the \xa77.1 contract.
    # ------------------------------------------------------------------

    def render(self):
        """Return a styled ``Text`` showing value (or placeholder).

        Returns one of the three render shapes the compositor knows
        how to drive (\xa77.1); we use ``Text`` so the cursor cell
        can carry a ``reverse`` style attribute distinct from the
        surrounding characters.

        Rendering rules:
          * Empty value AND non-empty placeholder -> show the
            placeholder dim; cursor sits at column 0 with reverse.
          * Empty value AND empty placeholder -> a single-space Text
            with reverse on column 0 (so the cursor is visible).
          * Non-empty value -> show the value (mask each char as
            U+2022 in password mode), reverse on cursor_position.
          * Cursor at end-of-line -> append a space so there is a
            cell to reverse-stylise.

        The styles used here are bare strings; the trimmed
        ``_rich.console`` parses them via Style.parse() at render
        time.  This keeps the widget free of per-style imports while
        still landing the right SGR bytes downstream.
        """
        # Decide what string to display.  Placeholder logic runs
        # before the password mask so a hidden password field still
        # shows the (cleartext) placeholder when empty - the
        # placeholder is documentation for the user, not a secret.
        value = self.value
        if not value:
            if self.placeholder:
                text = Text(self.placeholder, style="dim")
                # Cursor at column 0 even though there is no real
                # character there - the reverse highlight is applied
                # to the placeholder's first cell, which is the right
                # visual cue for "click here to start typing".
                if self.has_focus:
                    text.stylize("reverse", 0, 1)
                return text
            # Empty value and empty placeholder.  Render a single space
            # so the cursor cell is visible.
            text = Text(" ")
            if self.has_focus:
                text.stylize("reverse", 0, 1)
            return text

        # Non-empty value.  Mask in password mode.  Length is
        # preserved (cursor positions are still character offsets into
        # the plain value), so the mask is a per-char swap.
        if self.password:
            display = "•" * len(value)
        else:
            display = value

        # Append a trailing space if the cursor sits past the last
        # character (end-of-line cursor).  Without this, the reverse
        # style would have no cell to apply to.
        cursor = self.cursor_position
        if cursor >= len(display):
            display = display + " "

        text = Text(display)
        # Apply the cursor highlight.  Only when focused: an unfocused
        # Input still shows its value but no caret, mirroring upstream
        # Textual and most native UI conventions.
        if self.has_focus and 0 <= cursor < len(display):
            text.stylize("reverse", cursor, cursor + 1)
        return text
