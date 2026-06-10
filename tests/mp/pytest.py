"""Minimal pytest implementation for MicroPython.

Vendored from https://github.com/andrewleech/micropython-pytest
(master @ 2026-06-10) with local modifications, all candidates for
upstreaming:

  * argv items may be files or directories; the containing directory
    is put on sys.path and test modules import as top-level names, so
    nested test trees need no package __init__ chain.
  * run_test drives coroutine results through asyncio.run, so
    ``async def test_*`` works without a plugin.  mark.asyncio is the
    identity decorator for source compatibility with pytest-asyncio.
  * mark.skipif alias (upstream pytest spells it lowercase).
  * mark.parametrize accepts and ignores the ``ids=`` kwarg.
  * exit code derives from the overall error dict, not the last
    file's (upstream bug).

Fixture injection requires function.__code__.co_varnames, which
MicroPython does not currently expose (micropython/micropython#12280
closed unmerged; ceb8ba6 added __code__ without co_* attrs).  The
tests run by tests/mp/run.sh use no fixtures.
"""

import io
import os
import sys


fixtures_mapping = {}


def get_traceback(ex):
    buf = io.StringIO()
    if hasattr(sys, "print_exception"):
        sys.print_exception(ex, buf)
    else:
        import traceback
        traceback.print_exception(None, ex, None, file=buf)
    return buf.getvalue()


def getmembers(object, predicate=None):
    names = dir(object)
    members = [(n, getattr(object, n)) for n in names]
    if predicate:
        members = [(n, o) for n, o in members if predicate(o)]
    return members


def get_test_files():
    """Yield (display_path, module_name) for every test file selected
    by argv.  Each argv item is a test file or a directory to scan.
    The containing directory goes onto sys.path so the module imports
    as a top-level name."""
    targets = sys.argv[1:] if len(sys.argv) > 1 else [os.getcwd()]
    for target in targets:
        if target.endswith(".py"):
            dr, fname = target.rsplit("/", 1) if "/" in target else (".", target)
            files = [fname]
        else:
            dr = target
            files = sorted(
                f for f in os.listdir(dr)
                if f.startswith("test") and f.endswith(".py")
            )
        if dr not in sys.path:
            sys.path.insert(0, dr)
        for fname in files:
            yield f"{dr}/{fname}", fname[:-3]


def get_test_functions(module_name):
    module = __import__(module_name)
    for members in sorted(getmembers(module, callable)):
        if members[0].startswith("test_"):
            yield members
        elif "fixture_wrapper" in getattr(members[1], "__name__", ""):
            fixtures_mapping[members[0]] = members[1]


def _is_awaitable(obj):
    # MicroPython coroutines are generator-shaped; CPython coroutines
    # also expose send/throw.  Close enough for "the test returned
    # something that needs an event loop".
    return hasattr(obj, "send") and hasattr(obj, "throw")


def run_test(
    test_function_name,
    test_function_object,
    args,
    kwargs,
    passed,
    skipped,
    errors,
):
    try:
        result = test_function_object(*args, **kwargs)
        if _is_awaitable(result):
            import asyncio
            asyncio.run(result)
        passed.append(test_function_name)
        print(".", end="")
    except ParamResults as res:
        p, s, e = res.args[0]
        passed.extend(p)
        skipped.extend(s)
        errors.update(e)
    except Skipped:
        skipped.append(test_function_name)
        print("s", end="")
    except Exception as err:
        errors[test_function_name] = get_traceback(err)
        print("F", end="")


def test_runner():
    overall_passed = []
    overall_skipped = []
    overall_errors = {}

    for path, module in get_test_files():
        heading = path

        passed = []
        skipped = []
        errors = {}

        for test_function_name, test_function_object in get_test_functions(module):
            test_function_ident = f"{path}::{test_function_name}"
            if heading:
                print(f"\n{heading} ", end="")
                heading = ""

            try:
                test_args = test_function_object.__code__.co_varnames
            except AttributeError:
                if fixtures_mapping:
                    print("Error: Fixtures require function.__code__.co_varnames,"
                          " which this MicroPython build does not expose.")
                    raise SystemExit()
                test_args = []

            test_args_to_pass = []
            for arg in test_args:
                if arg in fixtures_mapping:
                    fixture_return_value = fixtures_mapping[arg]()
                    if hasattr(fixture_return_value, "__next__"):
                        test_args_to_pass.append(next(fixture_return_value))
                    else:
                        test_args_to_pass.append(fixture_return_value)
                else:
                    test_args_to_pass.append(arg)

            run_test(
                test_function_ident,
                test_function_object,
                test_args_to_pass,
                {},
                passed,
                skipped,
                errors,
            )

        overall_passed.extend(passed)
        overall_skipped.extend(skipped)
        overall_errors.update(errors)

    if overall_errors:
        print("\n\n====================== FAILURES ======================")
        for error_test_name, err in overall_errors.items():
            print(f"\nFAILED {error_test_name} failed with error:\n{err}")

    detail = ""
    if overall_errors:
        detail += f"{len(overall_errors)} failed, "
    detail += f"{len(overall_passed)} passed"
    if overall_skipped:
        detail += f", {len(overall_skipped)} skipped"
    print(f"\n====================== {detail} ======================")

    return 1 if overall_errors else 0


class raises:
    def __init__(self, exception):
        self.exception = exception

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            raise AssertionError(f"{self.exception} not raised")
        if exc_type is not self.exception:
            raise AssertionError(
                f"expected={self.exception} got \n {get_traceback(exc_val)}"
            ) from exc_val
        return True


def fixture(function):
    def fixture_wrapper():
        # doesn't support yielding functions
        return function()

    return fixture_wrapper


def skip(reason=""):
    raise Skipped(reason)


def _identity(function):
    return function


class mark:
    # pytest-asyncio source compatibility: the runner detects coroutine
    # results and drives them through asyncio.run regardless, so the
    # mark itself has nothing to do.
    asyncio = _identity

    @staticmethod
    def parametrize(keys, values, ids=None):
        def decorator(func):
            params = []
            for value in values:
                params.append(dict(zip([k.strip() for k in keys.split(",")], value)))

            def _parametrize_wrapper(*args, **kwargs):
                """Runs the original test function with multiple params"""
                passed = []
                skipped = []
                errors = {}

                for i, param in enumerate(params):
                    run_test(
                        f"{func.__name__}:{i}",
                        func,
                        [],
                        param,
                        passed,
                        skipped,
                        errors,
                    )

                raise ParamResults((passed, skipped, errors))

            return _parametrize_wrapper

        return decorator

    @staticmethod
    def skip(reason):
        def decorator(function):
            def skip_wrapper(*args, **kwargs):
                raise Skipped(reason)

            return skip_wrapper
        return decorator

    @staticmethod
    def skipIf(test, reason=""):
        def decorator(function):
            if test:
                return mark.skip(reason)(function)
            return function
        return decorator

    # upstream pytest spells it lowercase
    skipif = skipIf


class ParamResults(Exception):
    pass


class Skipped(Exception):
    pass


if __name__ == "__main__":
    sys.exit(test_runner())
