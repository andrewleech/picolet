"""Picolet — compile Python programs to small self-contained native binaries.

Public surface from the host-installed CLI:

  picolet.cli         — command implementations (build, dev, init, run, test, ...)
  picolet.testing     — AppHarness + webview/CDP drivers for autonomous testing
                        (requires the [testing] extra: ``pip install picolet[testing]``)
  picolet.templates   — starter-app templates discoverable via importlib.resources

The ``picolet`` CLI command is exposed via the entry point declared in
pyproject.toml; ``python -m picolet`` invokes the same main().

Note: the in-binary ``import picolet`` inside a compiled MicroPython runtime
refers to a separate set of frozen modules (the dispatcher, IPC transport,
system event facade, etc.) that live under packages/picolet-runtime/python/
and never ship in this wheel.
"""

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    __version__ = _pkg_version("picolet")
except Exception:
    __version__ = "0.0.0-dev"

__all__ = ("__version__",)
