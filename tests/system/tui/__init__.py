"""Phase 7 TuiHarness smoke tests.

These tests pre-date the picolet-tui binary itself — they stand the harness
up against shell-script stand-ins so the harness wiring (pty allocation,
parser feed, key-table dispatch, idle synchronisation) is exercised end-to-
end before the actual SUT exists.  Once Phase 4-5 land, widget tests
inherit this exact harness surface.
"""
