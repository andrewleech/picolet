"""test_objc_signatures.py — static analysis of objc_msgSend casts in
picolet_webview_mac.c (PH25).

This is a source-level test that runs on any host (including Linux CI).
It reads the C source, extracts every objc_msgSend cast via regex, and
checks that the argument count in the cast prototype is consistent with
the number of arguments actually passed at that call site.

The rule: a cast to
    RetType (*)(id, SEL, T1, ..., Tn)
implies the call site must pass exactly n extra arguments beyond (receiver,
selector).  We enforce that the number of cast parameters (excluding the
leading id + SEL pair) equals the number of extra arguments at the call.

This catches common memory-corruption bugs that arise from wrong-arity
casts of objc_msgSend without needing a Darwin host.
"""

import os
import re
import unittest

# Path to the C source file under test.
_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_SRC = os.path.join(
    _REPO_ROOT,
    "packages", "picolet-runtime", "overlay",
    "ports", "unix", "variants", "picolet-webview",
    "picolet_webview_mac.c",
)

# Regex: match a cast of objc_msgSend (or objc_msgSend_stret) to a function
# pointer type followed immediately by a call.
#
# Simplified form of what we look for:
#
#   ((RetType (*)(ParamList))objc_msgSend)(arg0, arg1, ...)
#
# We capture:
#   group 1: ParamList   — the parameter types in the cast
#   group 2: call_args   — the actual arguments passed to the call
#
# We deliberately limit to single-line patterns (no newline inside cast or
# call list) — the C source uses one-statement-per-line style throughout,
# so this is a safe assumption.
_CAST_RE = re.compile(
    r"\(\s*\(\s*\w[\w\s\*]*\(\s*\*\s*\)\s*\(([^)]*)\)\s*\)"  # cast: (*)(params)
    r"\s*objc_msgSend(?:_stret)?\s*\)"                          # )objc_msgSend[_stret])
    r"\s*\(([^;]*?)\)\s*;"                                      # (call args);
)


def _count_params(param_str):
    """Count comma-separated parameters in a C parameter list string.

    Handles nested angle brackets / parens at depth 0 only (sufficient
    for the simple types used in picolet_webview_mac.c).
    """
    if not param_str.strip() or param_str.strip() == "void":
        return 0
    depth = 0
    count = 1
    for ch in param_str:
        if ch in "(<":
            depth += 1
        elif ch in ")>":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def _count_args(args_str):
    """Count comma-separated arguments in a call argument string."""
    return _count_params(args_str)


class TestObjcMsgSendSignatures(unittest.TestCase):
    """Verify objc_msgSend cast arities match call argument counts."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(_SRC):
            raise unittest.SkipTest(
                "Source file not found: {}".format(_SRC)
            )
        with open(_SRC, "r") as fh:
            cls._source = fh.read()
        # Strip C-style block comments to avoid false matches in comments.
        cls._source_stripped = re.sub(r"/\*.*?\*/", " ", cls._source, flags=re.DOTALL)
        # Strip line comments.
        cls._source_stripped = re.sub(r"//[^\n]*", " ", cls._source_stripped)

    def test_source_file_present(self):
        """C source file exists at expected path."""
        self.assertTrue(os.path.isfile(_SRC), "Missing: {}".format(_SRC))

    def test_no_wrong_arity_casts(self):
        """Every objc_msgSend cast has matching parameter count vs call arity.

        The cast includes (id self, SEL _cmd, ...) so the total cast
        parameter count must equal 2 + len(extra_call_args).

        We tolerate _stret casts where the first parameter is a struct*
        (hidden return pointer) — those have an extra leading pointer arg.
        The test counts total cast params and total call args and checks
        they are equal.  This is the necessary condition; correctness of
        the types themselves requires a Darwin compiler.
        """
        src = self._source_stripped
        mismatches = []
        for m in _CAST_RE.finditer(src):
            cast_params = m.group(1).strip()
            call_args = m.group(2).strip()
            n_cast = _count_params(cast_params)
            n_call = _count_args(call_args)
            if n_cast != n_call:
                # Find approximate line number.
                line_no = src[: m.start()].count("\n") + 1
                mismatches.append(
                    "line ~{}: cast has {} params but call has {} args\n"
                    "  cast params: {}\n  call args:   {}".format(
                        line_no, n_cast, n_call,
                        cast_params[:80], call_args[:80],
                    )
                )
        self.assertEqual(
            mismatches, [],
            "objc_msgSend arity mismatches found:\n" + "\n\n".join(mismatches),
        )

    def test_all_exported_symbols_have_picolet_api(self):
        """All PICOLET_API-annotated function definitions are present for each
        symbol declared in the header."""
        # Read the header.
        header_path = _SRC.replace(".c", ".h")
        if not os.path.isfile(header_path):
            self.skipTest("Header not found: {}".format(header_path))
        with open(header_path, "r") as fh:
            header = fh.read()
        # Extract declared function names (lines ending in ;, preceded by
        # a return type).
        decl_re = re.compile(r"^\s*(?:int|void\s*\*|void|char\s*\*)\s+"
                             r"(picolet_wkwv_\w+)\s*\(", re.MULTILINE)
        declared = set(decl_re.findall(header))
        # Extract PICOLET_API definitions from the source.
        def_re = re.compile(r"PICOLET_API\s+(?:int|void\s*\*|void|char\s*\*)\s+"
                            r"(picolet_wkwv_\w+)\s*\(", re.MULTILINE)
        defined = set(def_re.findall(self._source))
        missing = declared - defined
        self.assertEqual(
            missing, set(),
            "Declared but not defined (missing PICOLET_API): {}".format(
                sorted(missing)
            ),
        )


if __name__ == "__main__":
    unittest.main()
