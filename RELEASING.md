# Releasing picolet

Two release pipelines live here:

- **Runtime artefacts** (`runtime-v*` tags) — the prebuilt MicroPython
  runtime binaries that `picolet build` downloads from GitHub Releases.
  Drives `.github/workflows/release.yml`.
- **PyPI CLI distribution** (`picolet-v*` tags) — the `picolet` package
  published to PyPI so end users can `uv tool install picolet`.  Drives
  `.github/workflows/pypi-publish.yml`.

The two pipelines are independent.  Most CLI releases will NOT need a new
runtime release; runtime releases are cut only when the underlying
MicroPython integration or variant config changes.

---

# Runtime artefact releases (`runtime-v*`)

How to cut a runtime release, what the CI pipeline publishes, and how
`picolet build` consumes the released artifacts.

## Tag pattern

Runtime releases use the tag pattern `runtime-v<semver>`:

```
runtime-v0.1.0
runtime-v0.1.1
runtime-v1.0.0
```

Pushing a tag matching `runtime-v*` to the remote triggers
`.github/workflows/release.yml`.

## What gets published

The release workflow builds a 3 × 2 matrix:

| Target | Variant | Artifact |
|---|---|---|
| `linux-x64` | `cli` | `picolet-runtime-linux-x64-cli` |
| `linux-x64` | `webview` | `picolet-runtime-linux-x64-webview` |
| `linux-x64` | `lvgl` | `picolet-runtime-linux-x64-lvgl` |
| `windows-x64` | `cli` | `picolet-runtime-windows-x64-cli.exe` |
| `windows-x64` | `webview` | `picolet-runtime-windows-x64-webview.exe` |
| `windows-x64` | `lvgl` | `picolet-runtime-windows-x64-lvgl.exe` |

Each artifact is uploaded together with two sidecars:

- `<artifact>.sha256` — SHA256 digest in `sha256sum` format; used by
  `picolet build` to verify download integrity.
- `<artifact>.cdx.json` — CycloneDX 1.5 SBOM listing all statically-linked
  components; merged into the app SBOM by `picolet build`.

A single workflow run produces all 18 files (6 binaries + 6 SHA256 + 6 SBOMs).

## Cutting a release

1. Ensure `packages/picolet-runtime/RUNTIME_TAG` contains the desired tag
   (e.g. `runtime-v0.1.0`). Commit if changed.

2. Tag the commit and push:

   ```bash
   git tag -s runtime-v0.1.0 -m "runtime-v0.1.0"
   git push origin runtime-v0.1.0
   ```

3. GitHub Actions starts the release workflow automatically. Monitor at:

   ```
   https://github.com/<org>/picolet/actions
   ```

4. When the workflow completes, verify the release at:

   ```
   https://github.com/<org>/picolet/releases/tag/runtime-v0.1.0
   ```

   All 18 files should be listed as release assets.

## Consuming from `picolet build`

`picolet build` resolves the runtime via `packages/picolet/picolet/runtime_resolver.py`.

Resolution order (first match wins):

1. `--runtime <path>` — explicit path override.
2. `--from-source` — build locally via `build-runtime.sh` (requires Docker).
3. Cache at `~/.cache/picolet/runtime/<tag>/<artifact>` (SHA256-verified).
4. Download from the GitHub Release CDN.
5. In-tree fallback at `packages/picolet-runtime/build/<artifact>`.

The tag used for download is read from (highest to lowest precedence):

- `PICOLET_RUNTIME_TAG` environment variable.
- `[runtime] tag` in the app's `picolet.toml`.
- `packages/picolet-runtime/RUNTIME_TAG` file (the default).

To pin an app to a specific runtime release, add to `picolet.toml`:

```toml
[runtime]
tag = "runtime-v0.1.0"
```

## Build matrix details

**Linux x64**: compiled inside `picolet-linux-x64-build:22.04` (Ubuntu 22.04 +
build-essential + libwebkit2gtk-4.1 + libsdl2-dev) to pin the minimum glibc
to 2.35 (NFR-8).

**Windows x64**: cross-compiled inside `dockcross/windows-static-x64-posix:latest`
(MinGW-w64 statically linked). Binaries run on Windows 10 21H2 and later (NFR-9).
The `lvgl` variant builds SDL2 from source with `-ffunction-sections` to keep
the binary under the 2 MiB NFR-3 ceiling.

## Pre-release dry-run procedure

Before pushing a real `runtime-v*` tag, validate the full release pipeline
using a throwaway tag on a fork or on this repo:

1. Push a test tag to trigger the workflow:

   ```bash
   git tag runtime-v0.0.0-test
   git push origin runtime-v0.0.0-test
   ```

2. Monitor the workflow run at:

   ```
   https://github.com/<org>/picolet/actions
   ```

   Confirm the 3 × 2 matrix expands to 6 parallel build jobs and all 6 complete
   successfully.

3. Verify all 18 release files land on the draft/pre-release:

   ```bash
   gh release view runtime-v0.0.0-test --json assets --jq '[.assets[].name] | sort'
   ```

   Expected: 6 binaries + 6 `.sha256` sidecars + 6 `.cdx.json` SBOMs.

4. Delete the test release and tag before pushing the real release tag:

   ```bash
   gh release delete runtime-v0.0.0-test --yes
   git push origin --delete runtime-v0.0.0-test
   git tag -d runtime-v0.0.0-test
   ```

This procedure catches cache key mismatches, upload path bugs, and
artifact naming regressions before they affect a production release.

## Re-running a release

Re-pushing a tag (after `git tag -f`) re-triggers the workflow. The
`gh release upload --clobber` step replaces existing assets idempotently.
Do not delete the release between re-runs — the workflow creates the release
only if it does not exist.

## SBOM policy

The runtime SBOM (`<artifact>.cdx.json`) lists all native components statically
linked into the binary. No GPL or AGPL components are statically linked (NFR-5).
LGPL components (WebKitGTK 4.1) are dynamically loaded at runtime and are
listed in the SBOM with `link_type = "dynamic"`.

`picolet build` merges the runtime SBOM with the app's own dependencies and
enforces the `[sbom]` policy from `picolet.toml` (see FR-SBOM-3 in
`docs/v1-spec.md`).

---

# PyPI CLI releases (`picolet-v*`)

The `picolet` PyPI distribution carries the host-side CLI tool — what
end users get when they run `uv tool install picolet`.  Published from
`packages/picolet/` via `.github/workflows/pypi-publish.yml`.

## Auth model: PyPI Trusted Publishing (OIDC)

No API tokens are stored anywhere.  GitHub Actions presents a signed OIDC
token to PyPI; PyPI verifies it matches the registered trusted publisher
for the project.  The publisher binding is a (owner, repo, workflow file)
tuple — anything outside that tuple cannot publish.

### One-time setup

One manual step, run once before the first publish.  Cannot be automated.

**Register a pending trusted publisher on PyPI.**

Sign in at https://pypi.org, then go to
https://pypi.org/manage/account/publishing/ → *Add a new pending publisher*.
Use exactly these values:

| Field | Value |
|---|---|
| **PyPI Project Name** | `picolet` |
| **Owner** | `andrewleech` |
| **Repository name** | `picolet` |
| **Workflow filename** | `pypi-publish.yml` |
| **Environment name** | `pypi` |

A "pending" publisher is needed because the project doesn't exist on PyPI
yet.  The first successful publish from the matching workflow creates the
project and graduates the publisher to active.

The `pypi` environment is a label-only scope — GitHub auto-creates it on
first workflow run with no protection rules, so tag push publishes
directly without any manual approval gate.

## Cutting a release

1. Bump the version in `packages/picolet/pyproject.toml`:

   ```toml
   [project]
   version = "0.2.1"
   ```

   Commit:

   ```bash
   git add packages/picolet/pyproject.toml
   git commit -s -m "[picolet] Bump to 0.2.1."
   git push origin dev
   ```

2. Tag and push:

   ```bash
   git tag -s picolet-v0.2.1 -m "picolet 0.2.1"
   git push origin picolet-v0.2.1
   ```

3. GitHub Actions runs end-to-end and publishes.  Monitor at
   https://github.com/andrewleech/picolet/actions.

4. Confirm the release landed at https://pypi.org/project/picolet/.

5. The first successful publish (one-time only) graduates the pending
   publisher to an active one.  No more pending-publisher entry needed
   for subsequent releases.

## Re-running a release

PyPI does NOT allow re-uploading the same version, even after a delete.
If the workflow fails mid-upload or you spot a bug after a successful
publish, bump the patch version and tag again.  Never `git tag -f` over
a previous `picolet-v*` tag.

## Build details

The workflow runs from a clean repo checkout:

1. `uv build` (inside `packages/picolet/`) — produces `dist/picolet-X.Y.Z-py3-none-any.whl`
   and `dist/picolet-X.Y.Z.tar.gz`.
2. Sanity check that the wheel contains `picolet/__main__.py` +
   `picolet/cli/`, `picolet/templates/`, `picolet/testing/`.
3. Tag-vs-pyproject version assertion — refuses to publish if the tag
   doesn't match the version in pyproject.toml.
4. `pypa/gh-action-pypi-publish@release/v1` uploads to PyPI using the
   OIDC token.

## Dry-running before the first real publish

Trusted Publishing has no built-in dry-run mode.  To verify the workflow
without affecting PyPI:

1. Build locally:

   ```bash
   cd packages/picolet && uv build
   ```

2. Inspect the wheel:

   ```bash
   unzip -l dist/picolet-*-py3-none-any.whl | head -30
   ```

3. Install into a temporary venv and exercise:

   ```bash
   uv tool install --reinstall ./packages/picolet
   picolet --version
   picolet init test-app --template hello-cli --output-dir /tmp/test-app
   ```

If all three steps pass, the CI workflow will too.

## Optional extras (`picolet[testing]`)

The `testing` extra brings in Playwright + websockets + Pillow.  This is
declared in `pyproject.toml`'s `[project.optional-dependencies]` table.
End users opt in with:

```bash
uv tool install 'picolet[testing]'
```

The base `picolet` install is intentionally lean — the CLI's core
workflow (`init` / `build` / `dev` / `run`) doesn't need the testing
deps.
