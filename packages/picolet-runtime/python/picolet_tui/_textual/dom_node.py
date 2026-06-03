"""picolet_tui._textual.dom_node - DOMNode base class.

A DOMNode is a tree node with an id, a class set, and a parent/children
relationship.  It sits between MessagePump (per-node queue + dispatch
loop) and Widget (renderable, mountable, focusable).

Spec coverage:
  * FR-TUI-9  - DOM tree with bindings, classes, id; subtree traversal.
  * FR-TUI-23 - mount() returns when the new subtree's on_mount has run.
  * FR-TUI-24 - unmount() is the inverse - depth-first dispose, parent
                unlink, message-pump shutdown.
  * FR-TUI-28 / R3 - the @widget decorator registration the runtime
                guard on Widget.__init__ keys off.  DOMNode itself is
                decorated (with empty meta) so the guard sees a valid
                _tui_widget_registered on the bottom of the MRO chain.

Design-doc references (textual-core-design.md):
  * §4.1 - DOMNode constructor + meta contributions.
  * §4.3 - mount / unmount lifecycle pseudo-code; this module ships
           the sync skeleton, the async wiring lives in Widget (§4.3)
           which composes the async on_mount call into mount().

The split between DOMNode and Widget is the one the design doc draws:
DOMNode owns the tree topology, Widget owns the render + focus + mount
async lifecycle.  Keeping the topology code here means tests that
exercise pure-tree operations (walk_children, ancestors, siblings) do
not need to instantiate a Widget - which is good, because Widget's R3
guard would reject an undecorated subclass.

What DOMNode does *not* do (deferred to Widget):
  * No render() method.  Pure topology has nothing to render.
  * No focus/blur.  Focus is a Widget-level concept (can_focus).
  * No refresh() / mark_dirty integration.  Refresh hooks the
    compositor, which only knows about renderable Widgets.
"""

# MessagePump is the substrate every DOMNode stands on.  Importing it
# here rather than at Widget level lets dispatch code rely on every
# DOMNode having a queue and a process_messages coroutine, which
# matters for the bubbling walk in §3.4 (it ascends through DOMNodes,
# not just Widgets).
from .message_pump import MessagePump

# NULL_REGION is the geometry sentinel for "no region assigned yet".
# Layout passes overwrite this; the compositor's mark_dirty walks
# children and skips nodes with region == NULL_REGION (see
# compositor.py mark_dirty), which is exactly the behaviour we want
# pre-layout.
from .geometry import NULL_REGION

# The @widget decorator is what populates `_tui_widget_meta` /
# `_tui_widget_registered`.  DOMNode itself must be decorated so the
# R3 guard in Widget.__init__ sees a valid registration on the base
# class.  Decorating with an empty body produces an empty-but-valid
# meta dict, which is the design's "DOMNode contributes BINDINGS = []"
# contract (§4.1).
from ._widget_decorator import widget


@widget
class DOMNode(MessagePump):
    """Tree node: bindings, classes, id, parent, children.

    The constructor mirrors the design doc §4.1 pseudo-code verbatim;
    keyword-only ``id`` / ``classes`` / ``parent`` matches the upstream
    Textual surface every test in the v0.1 suite calls into.

    ``classes`` is stored as a ``set`` rather than a list because the
    v0.2 selector engine (D2) does ``classes & required`` for intersection
    checks; using a set now means the upgrade path is a no-op.

    ``_region`` and ``_dirty`` are state the layout/composite pipeline
    writes through.  They live here (not on Widget) because the
    compositor walks the entire DOM, not just the Widget subset - a
    non-Widget DOMNode that somehow lands in the tree (test scaffolds,
    placeholder mixins) must still answer .region without crashing.
    """

    # Base BINDINGS - empty list at the DOMNode layer.  Subclasses
    # (Widget, Screen, App, user widgets) extend; the @widget MRO
    # merge concatenates parent-then-child so last-match-wins at key
    # dispatch (§6.2).
    BINDINGS = []

    def __init__(self, *, id=None, classes="", parent=None):
        # MessagePump.__init__ sets up the per-node queue, the wake
        # event, the parent pointer, and the empty _children list.
        # Passing parent through lets the dispatch walk see a
        # consistent _parent chain even before mount() runs.
        MessagePump.__init__(self, parent=parent)

        # Identity attributes the v0.1 selector engine (`#id`) keys on.
        # `id` is an immutable handle the author picks; `classes` is
        # a mutable set the framework may toggle (e.g. ":focused").
        self.id = id
        self.classes = set(classes.split()) if classes else set()

        # Topology slot.  MessagePump.__init__ already initialised
        # `self._children = []` but we shadow it here to make the
        # intent explicit at the DOMNode layer - a tree node's
        # children list, not just a pump's children list.  Both
        # references point at the same list object.
        self._children = self._children

        # Layout state.  NULL_REGION is the (0, 0, 0, 0) sentinel; the
        # first layout pass overwrites it.  ``_dirty`` is the per-node
        # "needs repaint" flag that refresh() flips; the compositor
        # reads it during render.
        self._region = NULL_REGION
        self._dirty = True

    # ------------------------------------------------------------------
    # Topology - parent/children accessors.
    # ------------------------------------------------------------------

    @property
    def parent(self):
        """The parent DOMNode, or None for the root.

        Property rather than direct ``_parent`` access because the
        upstream Textual surface exposes ``.parent`` and the v0.1
        tests we mirror call ``node.parent`` directly.  The setter
        is deliberately omitted - parent assignment goes through
        mount(), which keeps the children list in sync.
        """
        return self._parent

    @property
    def children(self):
        """The list of mounted child DOMNodes.

        Returned as the live list (not a copy) because the compositor's
        mark_dirty walks it in tight code (compositor.py §mark_dirty)
        and copying would be wasted bytes per frame.  Callers that
        mutate this list directly are violating the mount/unmount
        contract; the framework's only mutation paths go through
        mount() / unmount().
        """
        return self._children

    @property
    def region(self):
        """The region this node occupies, post-layout.

        NULL_REGION before the first layout pass.  The compositor's
        mark_dirty (compositor.py) reads this via getattr(n, "region")
        and skips NULL_REGION nodes, which is the correct behaviour
        for unmounted / pre-layout trees.
        """
        return self._region

    # ------------------------------------------------------------------
    # Mount / unmount - sync skeleton.
    # ------------------------------------------------------------------

    def mount(self, child):
        """Append ``child`` to this node's children and set parent ref.

        The sync skeleton of FR-TUI-23.  Widget.mount() composes this
        with the async on_mount call and the message-pump start, but
        a pure DOMNode mount is purely topological: link the child
        into the tree.  Returns ``child`` so callers can chain.

        The design doc §4.3 pseudo-code is async because Widget.mount
        starts the per-child process_messages task and awaits on_mount.
        DOMNode itself has no on_mount hook (those land at Widget),
        so the sync form is sufficient here and lets non-async test
        scaffolds build trees without an event loop.

        Idempotency: mounting a child that is already mounted under
        this parent is a no-op (the second append would duplicate the
        child in the list, which the compositor would then walk twice).
        Mounting a child currently mounted under a *different* parent
        unlinks it from the previous parent first - matches upstream
        Textual's "move" semantics.
        """
        if child._parent is self and child in self._children:
            # Already mounted here; nothing to do.  This guards against
            # double-mount through compose() + explicit mount() in
            # user code.
            return child

        if child._parent is not None and child._parent is not self:
            # Move: unlink from previous parent's children list before
            # relinking.  The previous parent's other state (region,
            # dirty flag) is left intact - it will get cleaned up the
            # next time its own mount/unmount path runs.
            try:
                child._parent._children.remove(child)
            except ValueError:
                # Already gone from the parent's list - tolerate.
                pass

        child._parent = self
        self._children.append(child)
        return child

    def unmount(self):
        """Remove this node from its parent, dispose the subtree.

        The sync skeleton of FR-TUI-24.  Walks the subtree depth-first
        (children before self) so descendant ``_parent`` pointers are
        cleared before this node detaches from its own parent - that
        ordering lets a child's ``.parent`` accessor return a sensible
        value (the about-to-be-orphaned ancestor) during its own
        teardown, rather than already-None.

        The async message-pump shutdown (cancelling process_messages,
        awaiting CancelledError) is Widget's responsibility - Widget
        overrides this to compose the async path.  For a pure DOMNode
        the sync form is enough: no pump means no task to cancel.

        Returns None; ``unmount`` is a void operation.
        """
        # Depth-first.  Walking a copy of the children list because the
        # recursive unmount() removes each child from the list as it
        # runs, and mutating the list while iterating it would skip
        # entries.
        for child in list(self._children):
            child.unmount()

        # Detach from parent.  The parent may already have unmounted
        # us (if we are inside its unmount() walk) - tolerate the
        # already-gone case.
        if self._parent is not None:
            try:
                self._parent._children.remove(self)
            except ValueError:
                pass
            self._parent = None

        # Clear topology so a re-mount sees a fresh slate; this also
        # breaks the parent->child strong ref the design §3.2 calls
        # out as the lifecycle-managed cycle.
        self._children = []

    # ------------------------------------------------------------------
    # Subtree traversal.
    # ------------------------------------------------------------------

    def walk_children(self, yield_root=False):
        """Yield every descendant of this node, depth-first.

        Args:
            yield_root: when True, yield ``self`` first.  Default False
                matches upstream Textual's surface (the caller typically
                already has the root and wants only descendants).

        The walk is depth-first, parent-before-children, left-to-right
        through the children list.  Compositor's mark_dirty uses an
        inline equivalent (compositor.py); we keep this method as the
        public, generator-shaped form for test code and for v0.2
        selector queries that need to scan the tree.

        Generator rather than list because the typical caller filters
        the output (e.g. "first focusable widget") and a list would
        copy the entire subtree only to discard most of it.
        """
        if yield_root:
            yield self
        for child in self._children:
            yield child
            # Recurse with yield_root=False because we already yielded
            # the child as our own descendant; yielding it again from
            # its own walk would double-emit.
            for descendant in child.walk_children(yield_root=False):
                yield descendant

    # ------------------------------------------------------------------
    # Ancestors / siblings.
    # ------------------------------------------------------------------

    @property
    def ancestors(self):
        """List of ancestors from immediate parent up to the root.

        Returned as a list (not a generator) because the typical caller
        - the bubbling walk in §3.4 and the App resolver in §4.4 -
        wants to indexed-access the root (``ancestors[-1]``).  The
        cost is O(depth), bounded by the v0.1 nesting limit (App ->
        Screen -> Widget -> child Widget); the doc puts typical
        depth at 5-10.

        The current node is NOT included; that is upstream Textual's
        convention and matches the surface FR-TUI tests assume.  Use
        ``ancestors_with_self`` if you need self at the head.
        """
        out = []
        node = self._parent
        while node is not None:
            out.append(node)
            node = node._parent
        return out

    @property
    def ancestors_with_self(self):
        """Same as ``ancestors`` but includes ``self`` at index 0.

        Provided because the bubbling walk in MessagePump.§3.4 does
        ``node = self; while node is not None: ... node = node._parent``
        which is equivalent to walking ``ancestors_with_self``.  Tests
        that want to assert the full chain at a given node use this
        accessor; the bubbling code keeps its inline walk for tight
        per-message cost.
        """
        return [self] + self.ancestors

    @property
    def siblings(self):
        """List of sibling DOMNodes (same parent, excluding self).

        Empty list at the root.  Order matches the parent's children
        list (insertion order), which is the order compose() yielded
        and mount() appended.  Returned as a list because the typical
        caller iterates it more than once (focus-next + focus-previous
        navigation), so paying the O(n) build cost once is cheaper
        than re-running a generator.
        """
        if self._parent is None:
            return []
        return [c for c in self._parent._children if c is not self]
