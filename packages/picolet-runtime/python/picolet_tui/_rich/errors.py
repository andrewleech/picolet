"""
Rich ``errors`` exception classes, ported for picolet-tui.

Upstream: Textualize/rich master @ d97751b1590be7d5443f2466cf40d0ea6ea56ed5
(``rich/errors.py``). The upstream file is pure ``class X(Exception):``
declarations and runs unmodified under MicroPython — no imports, no
decorators, no metaclasses. This port reproduces the public surface
verbatim.

REMOVED vs upstream:
  Nothing. The whole module is in scope: the Rich subset listed in
  research doc 02 calls out every one of these classes as something a
  later-tier Rich module raises (markup parser, style parser, console
  renderable check, live display, alt-screen guard). Stripping any of
  them would force matching edits in those later ports.

NOT ADDED here:
  The picolet-tui-level exceptions called out in FR-TUI-77
  (``PicoletTuiError``, ``ReactiveError``, ``TooManyComputesError``,
  ``MissingWidgetDecoratorError``, ``StyleError``, ``HarnessError``,
  ``PtyAllocError``) live in ``picolet_tui.errors``, NOT in the Rich
  subset. The Rich ``StyleError`` defined here is the legacy Rich one
  (parent of ``MissingStyle``) and is distinct from the FR-TUI-77
  framework ``StyleError``; callers that need the framework class
  should import ``picolet_tui.errors.StyleError`` explicitly.

Spec coverage:
  Indirect support for FR-TUI-22..34 (style/markup parsing performed by
  later Rich tier modules — ``style.py``, ``markup.py``, ``console.py``,
  ``live.py``, ``screen.py`` — which raise these exceptions on bad
  input). Not itself bound to a single FR/NFR id.
"""


class ConsoleError(Exception):
    """An error in console operation."""


class StyleError(Exception):
    """An error in styles."""


class StyleSyntaxError(ConsoleError):
    """Style was badly formatted."""


class MissingStyle(StyleError):
    """No such style."""


class StyleStackError(ConsoleError):
    """Style stack is invalid."""


class NotRenderableError(ConsoleError):
    """Object is not renderable."""


class MarkupError(ConsoleError):
    """Markup was badly formatted."""


class LiveError(ConsoleError):
    """Error related to Live display."""


class NoAltScreen(ConsoleError):
    """Alt screen mode was required."""
