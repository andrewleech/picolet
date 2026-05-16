# Software Bill of Materials (SBOM)

Picolet pulls in third-party code that ships inside every produced binary.
This document captures the SBOM tracking plan: how dependencies are
catalogued, where their licences live in the build output, and what the
release pipeline emits alongside each artifact.

## Why this matters

A Picolet user receives a single executable. That executable can contain:

| Component | Licence | Link type |
|---|---|---|
| MicroPython core | MIT | static |
| libffi | MIT-style | static (built from source per platform) |
| WebView2 loader | proprietary, redistributable | dynamic (Windows) |
| WebKitGTK | LGPL-2.1+ | dynamic (Linux, system) |
| LVGL | MIT | static |
| lv_binding_micropython | MIT | static |
| Selected `micropython-lib` modules | per-module, mostly MIT | static (frozen `.mpy`) |
| App Python sources | user's choice | static (frozen `.mpy`) |
| Frontend assets | user's choice | static (romfs) |

LGPL-linked components carry relinking obligations. Apple frameworks
carry redistribution-only allowances. Static MIT components must
propagate their copyright notices. None of this is optional for an
open-source framework that ships pre-built binaries.

## Format

Picolet emits a [CycloneDX 1.5](https://cyclonedx.org/) JSON document
per runtime artifact and per built application. CycloneDX is the format
adopted by SPDX/CISA for software supply-chain inventories and has
mature tooling.

Each release artifact carries its SBOM as a sibling file:

```
picolet-runtime-windows-x64-webview.bin
picolet-runtime-windows-x64-webview.bin.cdx.json
```

Application SBOMs emitted by `picolet build` sit alongside the binary in
`target/`:

```
my-app.exe
my-app.exe.cdx.json
```

## Sources of truth

The SBOM generator walks three inputs and deduplicates:

1. **`packages/picolet-runtime/sbom/runtime.toml`** — declares each native
   dependency, its licence (SPDX id), source URL, version pin, and link
   type (`static`, `dynamic`, `build-time-only`). Hand-maintained.
2. **`packages/picolet-runtime/mbm.toml`** — captures the MicroPython
   upstream PRs that feed the integration branch. Already in use for
   integration management.
3. **App `picolet.toml [dependencies]`** — captures Python libraries the
   user's app freezes in (micropython-lib modules, local packages, PyPI
   pulls). Each entry resolves to a licence via the source registry's
   metadata.

A fourth implicit input is the `micropython-lib` registry, which is
consulted to fill licences for `require(...)` entries from the
`unix-ffi` index and friends.

## Embedding vs. external

The SBOM is emitted as an external sibling file. It is **not** embedded
in the binary itself by default — embedding bloats every binary by
~5 KB per renderer's dependency set, which is meaningful at the
~750 KB target. A future `[sbom] embed = true` flag can opt in for
field-audit scenarios.

## Build-time enforcement

`picolet build` checks the resolved dependency list against a per-app
allowlist:

```toml
[sbom]
allow_licences = ["MIT", "BSD-3-Clause", "Apache-2.0", "0BSD"]
allow_dynamic = ["LGPL-2.1-or-later", "proprietary"]   # dynamic only
warn_unknown = true
fail_unknown = false
```

Defaults are conservative (warn rather than fail) so projects can
iterate. CI in the Picolet release pipeline runs with `fail_unknown =
true` against the runtime's own SBOM.

## LGPL relinking

For LGPL components reached dynamically, the LGPL-2.1 §6 relinking
obligation is satisfied by:

- Distributing the dynamically-loaded library separately, in its
  upstream form, with its source URL recorded in the SBOM.
- Supporting `picolet build --from-source` so any user can re-derive the
  runtime binary from the published source tree.

Affected components:

- **WebKitGTK 4.1** (Linux): system-provided; Picolet ships no copy.
  Source URL in `sbom/runtime.toml`.
- **WebView2 runtime** (Windows): distributed separately by Microsoft as
  an operating-system component. The `WebView2Loader.dll` redistributable
  is not LGPL; it is covered by Microsoft's proprietary fixed-terms
  licence. LGPL does not apply to it.
- **lv_binding_micropython** (Linux + Windows, both variants): linked
  statically (MIT); LGPL does not apply.

## Open questions

- Whether to switch from CycloneDX to SPDX 2.3 if downstream regulatory
  contexts (e.g. medical-device SBOM submissions) push that way.
- How to express MicroPython port-specific code (e.g. the pydfu-win
  PRs) — they are MicroPython-licensed but not yet upstream. Likely
  treated as a forked component with a separate SBOM entry until
  merged.
- Whether the framework's SBOM-generation tool itself should live in
  `picolet-cli` or in a separate `picolet-sbom` package. Leaning toward
  in-tree under `picolet-cli` for v1, split out if it grows.
