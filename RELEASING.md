# Releasing picolet runtimes

This document describes how to cut a runtime release, what the CI pipeline
publishes, and how `picolet build` consumes the released artifacts.

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

`picolet build` resolves the runtime via `packages/picolet-cli/picolet/runtime_resolver.py`.

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
