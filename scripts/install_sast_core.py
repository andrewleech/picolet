#!/usr/bin/env python3
"""Install and verify the pinned shared mpy_analysis core from an explicit MicroPython checkout.

The revision in scripts/sast/shared-core.json is exported with `git archive` from the
checkout named by --source, so the installed code is exactly that commit regardless of the
checkout's current branch or working-tree state. The installed package is then compared
file-for-file with the exported tree and the receipt records the verified revision.

There is no network fetch and no fallback source: a checkout that does not contain the
pinned commit fails the install.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

PIN_PATH = Path(__file__).resolve().parent / "sast" / "shared-core.json"


class CoreInstallError(RuntimeError):
    pass


def load_pin(path: Path = PIN_PATH) -> dict[str, Any]:
    pin = json.loads(path.read_text(encoding="utf-8"))
    for key in ("distribution", "package", "subdirectory", "revision"):
        if not isinstance(pin.get(key), str) or not pin[key]:
            raise CoreInstallError(f"{path} requires a nonempty {key!r}")
    if len(pin["revision"]) != 40 or any(c not in "0123456789abcdef" for c in pin["revision"]):
        raise CoreInstallError(f"{path} revision must be a full lowercase commit SHA")
    return pin


def _git(source: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(source), *args], capture_output=True, check=False)


def export_revision(source: Path, pin: dict[str, Any], destination: Path) -> Path:
    """Extract the pinned package tree into destination; return the package project root."""
    revision = pin["revision"]
    resolved = _git(source, "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
    if resolved.returncode or resolved.stdout.decode().strip() != revision:
        raise CoreInstallError(
            f"shared core revision {revision} is not present in {source}; it is not registered "
            "in the composed MicroPython integration and no remote carries it"
        )
    archive = _git(source, "archive", "--format=tar", revision, pin["subdirectory"])
    if archive.returncode:
        raise CoreInstallError(f"git archive failed: {archive.stderr.decode().strip()}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                raise CoreInstallError(f"unexpected archive member type: {member.name}")
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise CoreInstallError(f"unsafe archive member path: {member.name}")
            inside = member.name == pin["subdirectory"] or member.name.startswith(
                pin["subdirectory"] + "/"
            )
            # git archive also emits the directories leading to the requested path.
            ancestor = member.isdir() and (pin["subdirectory"] + "/").startswith(member.name + "/")
            if not (inside or ancestor):
                raise CoreInstallError(f"archive member outside {pin['subdirectory']}: {member.name}")
        if sys.version_info >= (3, 12):
            tar.extractall(destination, filter="data")
        else:
            tar.extractall(destination)
    return destination / pin["subdirectory"]


def _installer(python: str) -> list[str]:
    if subprocess.run([python, "-m", "pip", "--version"], capture_output=True, check=False).returncode == 0:
        return [python, "-m", "pip", "install"]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", python]
    raise CoreInstallError(f"{python} has neither pip nor an available uv to install with")


def install(project: Path, python: str, target: Path | None) -> None:
    command = _installer(python)
    if target is not None:
        # A reused target directory must be replaced, not left holding a previous install.
        command.append("--reinstall" if command[0] != python else "--upgrade")
        command.extend(["--target", str(target)])
    command.append(str(project))
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise CoreInstallError(f"installer exited {result.returncode}")


def _files(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def _installed_package(python: str, package: str, target: Path | None) -> Path:
    env = dict(os.environ)
    if target is not None:
        env["PYTHONPATH"] = str(target)
    else:
        env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [python, "-c", f"import {package}; print({package}.__file__)"],
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode:
        raise CoreInstallError(f"{package} is not importable by {python}: {result.stderr.decode().strip()}")
    return Path(result.stdout.decode().strip()).resolve().parent


_METADATA_PROBE = (
    "import importlib.metadata as m, json, sys\n"
    "d = m.distribution(sys.argv[1])\n"
    "print(json.dumps({'version': d.version, 'requires_python': d.metadata['Requires-Python'],"
    " 'requires': d.requires or [],"
    " 'scripts': {e.name: e.value for e in d.entry_points if e.group == 'console_scripts'}}))\n"
)


def _normalized(requirements: list[str]) -> list[str]:
    return sorted("".join(requirement.split()).lower() for requirement in requirements)


def _pinned_metadata(project: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError as exc:
        raise CoreInstallError("verifying package metadata requires Python 3.11 or newer (tomllib)") from exc
    declared = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {
        "version": declared["version"],
        "requires_python": declared["requires-python"],
        "requires": _normalized(declared.get("dependencies", [])),
        "scripts": declared.get("scripts", {}),
    }


def _installed_metadata(python: str, distribution: str, target: Path | None) -> dict[str, Any]:
    env = dict(os.environ)
    if target is not None:
        env["PYTHONPATH"] = str(target)
    else:
        env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [python, "-c", _METADATA_PROBE, distribution], capture_output=True, env=env, check=False
    )
    if result.returncode:
        raise CoreInstallError(
            f"distribution {distribution} metadata is not readable by {python}: {result.stderr.decode().strip()}"
        )
    metadata = json.loads(result.stdout.decode())
    metadata["requires"] = _normalized(metadata["requires"])
    return metadata


def verify(project: Path, pin: dict[str, Any], python: str, target: Path | None) -> dict[str, Any]:
    """Require the installed package to be the pinned tree; return the receipt."""
    installed = _installed_package(python, pin["package"], target)
    expected = _files(project / pin["package"])
    actual = _files(installed)
    # Package data is selected by the project's own packaging rules, so an installed
    # tree may omit non-package files but never contain different or extra ones.
    extra = sorted(set(actual) - set(expected))
    changed = sorted(name for name in actual if name in expected and actual[name] != expected[name])
    missing = sorted(name for name in expected if name.endswith(".py") and name not in actual)
    if extra or changed or missing:
        raise CoreInstallError(
            f"installed {pin['package']} at {installed} is not revision {pin['revision']}: "
            f"extra={extra} changed={changed} missing={missing}"
        )
    pinned = _pinned_metadata(project)
    metadata = _installed_metadata(python, pin["distribution"], target)
    mismatched = sorted(key for key in pinned if pinned[key] != metadata[key])
    if mismatched:
        raise CoreInstallError(
            f"installed {pin['distribution']} metadata differs from the pinned pyproject in {mismatched}: "
            f"installed={ {key: metadata[key] for key in mismatched} } pinned={ {key: pinned[key] for key in mismatched} }"
        )
    return {
        "distribution": pin["distribution"],
        "revision": pin["revision"],
        "subdirectory": pin["subdirectory"],
        "installed_package": str(installed),
        "python": python,
        "files_verified": len(actual),
        "metadata_verified": sorted(pinned),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="MicroPython checkout containing the pinned commit")
    parser.add_argument("--python", default=sys.executable, help="Interpreter that will import the core")
    parser.add_argument("--target", type=Path, help="pip --target directory (import it with PYTHONPATH)")
    parser.add_argument("--receipt", type=Path, help="Write the verified install receipt here")
    parser.add_argument("--verify-only", action="store_true", help="Check an existing install without installing")
    args = parser.parse_args(argv)
    try:
        pin = load_pin()
        with tempfile.TemporaryDirectory(prefix="picolet-sast-core-") as temp:
            project = export_revision(args.source, pin, Path(temp))
            if not args.verify_only:
                install(project, args.python, args.target)
            receipt = verify(project, pin, args.python, args.target)
    except (CoreInstallError, OSError, ValueError) as exc:
        print(f"shared analysis core: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
