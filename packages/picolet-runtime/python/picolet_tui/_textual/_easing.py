"""picolet_tui._textual._easing - normalized easing curves ported from Textual.

Upstream provenance
-------------------
Ported from Textual main
  https://github.com/Textualize/textual/blob/main/src/textual/_easing.py
Roughly 130 LoC of pure-math curve definitions, originally translated
by upstream from https://easings.net/.

Why we keep this in v0.1
------------------------
Synthesis D7 puts animation out of the v0.1 scope - the App in
``picolet_tui`` does not schedule animation frames and no widget driver
calls ``EASING[name](t)`` today.  The curves themselves are however:

  * dependency-free (only ``math`` - all of cos/sin/sqrt/pi/pow are in
    the MicroPython core, no shim required, NFR-TUI-13 clean),
  * tiny (one dict, eleven helpers, no closures over outer state),
  * already referenced by the Style DSL parser via the
    ``EASING.keys()`` set when validating ``transition:`` rules
    (FR-TUI-32..37, D2).  Stripping the table would mean either
    inventing a separate "valid easing names" set or rejecting CSS
    that downstream Textual examples copy verbatim.

So the table ships frozen, the helpers ship inert, and Phase 5+ can
wire ``Style.transition`` to actually drive a value through one of
these curves without another file landing.

What was REMOVED vs upstream
----------------------------
* The PEP 484 ``-> float`` / ``: float`` annotations are dropped on
  the helper signatures.  MicroPython parses them but they cost
  frozen-bytes (NFR-TUI-19) and add no runtime value here - every
  caller already passes a float in [0, 1] and gets a float back.
  The lambdas in ``EASING`` were never annotated upstream.

* Upstream's ``# x in (0, 1)`` trailing comments on the fallback
  branches of ``_in_out_expo`` / ``_in_elastic`` / ``_in_out_elastic``
  / ``_out_elastic`` are corrected to ``# x is 0 or 1`` - the original
  reads as "x is inside the open interval" which is the opposite of
  the branch that actually fires (x == 0 or x == 1, where the curve
  must pin to the identity to keep f(0)=0 and f(1)=1).  Behaviour is
  unchanged.

What was KEPT vs upstream
-------------------------
* ``DEFAULT_EASING = "in_out_cubic"`` and
  ``DEFAULT_SCROLL_EASING = "out_cubic"`` - the Style DSL parser
  (Phase 5) will read these to fill in unspecified ``transition:`` /
  ``scrollbar-`` defaults so behaviour matches upstream Textual
  documentation.

* All 32 named curves, in upstream insertion order, so any future
  CSS-compat layer that round-trips through ``list(EASING)`` produces
  byte-identical output.

Shim gaps
---------
None.  ``math`` is a MicroPython built-in (cos, sin, sqrt, pi all
present); ``pow`` is a builtin.  This is the simplest file in the
port.
"""

from math import cos, pi, sin, sqrt


def _in_out_expo(x):
    """https://easings.net/#easeInOutExpo"""
    if 0 < x < 0.5:
        return pow(2, 20 * x - 10) / 2
    elif 0.5 <= x < 1:
        return (2 - pow(2, -20 * x + 10)) / 2
    else:
        return x  # x is 0 or 1 - pass through so f(0)=0 and f(1)=1


def _in_out_circ(x):
    """https://easings.net/#easeInOutCirc"""
    if x < 0.5:
        return (1 - sqrt(1 - pow(2 * x, 2))) / 2
    else:
        return (sqrt(1 - pow(-2 * x + 2, 2)) + 1) / 2


def _in_out_back(x):
    """https://easings.net/#easeInOutBack"""
    # 1.70158 is the "back" overshoot constant from easings.net; multiplying
    # by 1.525 gives the symmetric in-out variant its slightly larger swing.
    c = 1.70158 * 1.525
    if x < 0.5:
        return (pow(2 * x, 2) * ((c + 1) * 2 * x - c)) / 2
    else:
        return (pow(2 * x - 2, 2) * ((c + 1) * (x * 2 - 2) + c) + 2) / 2


def _in_elastic(x):
    """https://easings.net/#easeInElastic"""
    c = 2 * pi / 3
    if 0 < x < 1:
        return -pow(2, 10 * x - 10) * sin((x * 10 - 10.75) * c)
    else:
        return x  # x is 0 or 1 - identity preserves the curve endpoints


def _in_out_elastic(x):
    """https://easings.net/#easeInOutElastic"""
    c = 2 * pi / 4.5
    if 0 < x < 0.5:
        return -(pow(2, 20 * x - 10) * sin((20 * x - 11.125) * c)) / 2
    elif 0.5 <= x < 1:
        return (pow(2, -20 * x + 10) * sin((20 * x - 11.125) * c)) / 2 + 1
    else:
        return x  # x is 0 or 1 - identity preserves the curve endpoints


def _out_elastic(x):
    """https://easings.net/#easeOutElastic"""
    c = 2 * pi / 3
    if 0 < x < 1:
        return pow(2, -10 * x) * sin((x * 10 - 0.75) * c) + 1
    else:
        return x  # x is 0 or 1 - identity preserves the curve endpoints


def _out_bounce(x):
    """https://easings.net/#easeOutBounce"""
    # Magic constants come straight from easings.net; they're the piecewise
    # parabola tuning that makes the four bounces decay visibly.
    n, d = 7.5625, 2.75
    if x < 1 / d:
        return n * x * x
    elif x < 2 / d:
        x_ = x - 1.5 / d
        return n * x_ * x_ + 0.75
    elif x < 2.5 / d:
        x_ = x - 2.25 / d
        return n * x_ * x_ + 0.9375
    else:
        x_ = x - 2.625 / d
        return n * x_ * x_ + 0.984375


def _in_bounce(x):
    """https://easings.net/#easeInBounce"""
    return 1 - _out_bounce(1 - x)


def _in_out_bounce(x):
    """https://easings.net/#easeInOutBounce"""
    if x < 0.5:
        return (1 - _out_bounce(1 - 2 * x)) / 2
    else:
        return (1 + _out_bounce(2 * x - 1)) / 2


# Insertion order matters - the Style DSL parser (Phase 5) and any future
# CSS-compat layer iterate this dict to validate ``transition:`` names, and
# upstream Textual docs list them in this exact sequence.  Do not reorder.
EASING = {
    "none": lambda x: 1.0,
    "round": lambda x: 0.0 if x < 0.5 else 1.0,
    "linear": lambda x: x,
    "in_sine": lambda x: 1 - cos((x * pi) / 2),
    "in_out_sine": lambda x: -(cos(x * pi) - 1) / 2,
    "out_sine": lambda x: sin((x * pi) / 2),
    "in_quad": lambda x: x * x,
    "in_out_quad": lambda x: 2 * x * x if x < 0.5 else 1 - pow(-2 * x + 2, 2) / 2,
    "out_quad": lambda x: 1 - pow(1 - x, 2),
    "in_cubic": lambda x: x * x * x,
    "in_out_cubic": lambda x: 4 * x * x * x if x < 0.5 else 1 - pow(-2 * x + 2, 3) / 2,
    "out_cubic": lambda x: 1 - pow(1 - x, 3),
    "in_quart": lambda x: pow(x, 4),
    "in_out_quart": lambda x: 8 * pow(x, 4) if x < 0.5 else 1 - pow(-2 * x + 2, 4) / 2,
    "out_quart": lambda x: 1 - pow(1 - x, 4),
    "in_quint": lambda x: pow(x, 5),
    "in_out_quint": lambda x: 16 * pow(x, 5) if x < 0.5 else 1 - pow(-2 * x + 2, 5) / 2,
    "out_quint": lambda x: 1 - pow(1 - x, 5),
    # ``in_expo`` / ``out_expo`` guard the endpoint because pow(2, -inf) and
    # pow(2, 0) would give the wrong asymptote at x in {0, 1}.
    "in_expo": lambda x: pow(2, 10 * x - 10) if x else 0,
    "in_out_expo": _in_out_expo,
    "out_expo": lambda x: 1 - pow(2, -10 * x) if x != 1 else 1,
    "in_circ": lambda x: 1 - sqrt(1 - pow(x, 2)),
    "in_out_circ": _in_out_circ,
    "out_circ": lambda x: sqrt(1 - pow(x - 1, 2)),
    "in_back": lambda x: 2.70158 * pow(x, 3) - 1.70158 * pow(x, 2),
    "in_out_back": _in_out_back,
    "out_back": lambda x: 1 + 2.70158 * pow(x - 1, 3) + 1.70158 * pow(x - 1, 2),
    "in_elastic": _in_elastic,
    "in_out_elastic": _in_out_elastic,
    "out_elastic": _out_elastic,
    "in_bounce": _in_bounce,
    "in_out_bounce": _in_out_bounce,
    "out_bounce": _out_bounce,
}

# Picked to match upstream Textual; the Style DSL parser (Phase 5) reads
# these when a ``transition:`` rule omits the curve name.
DEFAULT_EASING = "in_out_cubic"
DEFAULT_SCROLL_EASING = "out_cubic"
