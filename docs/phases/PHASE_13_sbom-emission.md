# PH13 — SBOM emission

## Plan

### Goal

Emit a CycloneDX 1.5 JSON SBOM as a sibling file for every runtime
artifact and every `picolet build` output.

This phase closes:

| Spec id | Requirement |
|---|---|
| FR-SBOM-1 | Each runtime artifact and each `picolet build` output carries a sibling `<artifact>.cdx.json` in CycloneDX 1.5 format. |
| FR-SBOM-2 | The app SBOM is the union of: the runtime artifact's SBOM, the user's app `[dependencies]`, and the frozen micropython-lib modules pulled in by the manifest. |
| FR-SBOM-3 | `picolet build` consults `[sbom] allow_licences` and `[sbom] allow_dynamic` and either warns or fails per `[sbom] fail_unknown`. |

It also satisfies NFR-5 (no static GPL/AGPL link) and NFR-7 (CI
pipeline emits SBOMs alongside artifacts) — PH15 consumes the SBOM
generator introduced here. NFR-6 (all Picolet-authored code is MIT) is
documented in `runtime.toml` in this phase.

### Architecture decisions

#### AD1 — `runtime.toml` schema

`packages/picolet-runtime/sbom/runtime.toml` is a hand-maintained TOML
file declaring every native dependency that ships inside a built
runtime. It is the authoritative source for components that are not
discoverable from Python package metadata (C libraries, vendored DLLs,
submodule-pinned bindings).

Each component is an entry in a `[[component]]` array:

```toml
[[component]]
name          = "MicroPython"
version       = "1.24.0"
licence       = "MIT"            # SPDX expression
source_url    = "https://github.com/micropython/micropython"
link_type     = "static"         # static | dynamic | build-time-only
variants      = ["cli", "webview", "lvgl"]  # omit = all variants
targets       = ["linux-x64", "windows-x64"] # omit = all targets
purl          = "pkg:github/micropython/micropython@1.24.0"  # optional
notes         = ""               # optional free text
```

Required keys: `name`, `version`, `licence`, `source_url`, `link_type`.
Optional keys: `variants`, `targets`, `purl`, `notes`.

`link_type` meanings:
- `static` — object code is part of the runtime binary.
- `dynamic` — loaded at runtime; binary carries no copy.
- `build-time-only` — used only during the build; absent from the
  shipped artifact (e.g. dockcross toolchain).

`variants` and `targets` act as inclusion filters. An entry without
either key applies to all six runtime artifacts. An entry with
`variants = ["webview"]` appears in the webview SBOM only. An entry
with `targets = ["windows-x64"]` appears in windows builds only.

The complete initial component list, derived from PH10–PH12 carryover
notes and `docs/sbom.md`:

| name | version | licence | link_type | variants | targets |
|---|---|---|---|---|---|
| MicroPython | 1.24.0 | MIT | static | (all) | (all) |
| libffi | 3.4.6 | MIT | static | (all) | (all) |
| lv_binding_micropython | SHA `4a569cd` | MIT | static | [lvgl] | (all) |
| LVGL | 9.x | MIT | static | [lvgl] | (all) |
| SDL2 | 2.0.20 | Zlib | dynamic | [lvgl] | [linux-x64] |
| SDL2 | 2.28.x | Zlib | static | [lvgl] | [windows-x64] |
| WebKitGTK | 4.1 | LGPL-2.1-or-later | dynamic | [webview] | [linux-x64] |
| Microsoft.Web.WebView2 | 1.0.2210.55 | LicenseRef-MS-WebView2-Fixed | dynamic | [webview] | [windows-x64] |
| WebView2Loader.dll | 1.0.2210.55 | LicenseRef-MS-WebView2-Fixed | dynamic | [webview] | [windows-x64] |
| WebView2_min.h (derived) | picolet-authored | MIT | build-time-only | [webview] | [windows-x64] |

The `LicenseRef-MS-WebView2-Fixed` SPDX user-defined expression records
that WebView2 is redistributable under Microsoft's fixed terms but is
not an OSI-approved SPDX id. The `notes` field on those entries cites
the governing licence URL.

The seven MicroPython integration PRs from `mbm.toml` are treated as
MicroPython-derived patches, not independent components. They share the
MicroPython MIT licence and are listed in the MicroPython component's
`notes` field (PR numbers + titles), not as separate `[[component]]`
entries. The SBOM generator reads the PR list from `mbm.toml`
automatically.

#### AD2 — SBOM generator location and interface

The generator lives at `packages/picolet-cli/picolet/sbom_gen.py`. This
keeps all build-pipeline tooling in one Python package, matching the
existing pattern for `build_cmd.py`, `validator.py`, `runtime_resolver.py`.

The generator exposes one public function used by `build_cmd.py`:

```python
def emit_app_sbom(
    output_path: Path,           # e.g. target/linux-x64/myapp.cdx.json
    runtime_sbom_path: Path,     # .cdx.json sidecar from resolved runtime
    app_data: dict,              # parsed picolet.toml
    target: str,                 # "linux-x64" | "windows-x64"
    variant: str,                # "cli" | "webview" | "lvgl"
    repo_root: Path,
) -> list[SbomViolation]:        # empty = clean; see FR-SBOM-3
    ...
```

And one used by the runtime build pipeline (called by
`build-runtime.sh` post-build, or invoked directly in PH15's CI):

```python
def emit_runtime_sbom(
    output_path: Path,           # e.g. build/picolet-runtime-linux-x64-cli.cdx.json
    target: str,
    variant: str,
    repo_root: Path,
) -> None:
    ...
```

Both functions write a CycloneDX 1.5 JSON document to `output_path`.
`emit_app_sbom` additionally returns any policy violations
(`SbomViolation` is a dataclass with fields `component`, `reason`,
`severity: "warn" | "fail"`).

The generator also has a thin CLI shim so the runtime build script can
call it without importing `picolet-cli` as a library:

```
python -m picolet.sbom_gen emit-runtime \
    --output <path> --target <t> --variant <v> --repo-root <r>
```

#### AD3 — CycloneDX 1.5 JSON output shape

The generator emits a minimal but spec-valid CycloneDX 1.5 JSON
document. No external CycloneDX library is required (the format is
well-bounded for this use-case; a dependency on `cyclonedx-python-lib`
would add a transitive dep chain to `picolet-cli` for marginal benefit
at this scale).

Minimal valid shape:

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "serialNumber": "urn:uuid:<uuid4>",
  "version": 1,
  "metadata": {
    "timestamp": "<ISO 8601 UTC>",
    "tools": [{"vendor": "picolet", "name": "picolet-sbom-gen", "version": "<picolet version>"}],
    "component": {
      "type": "application",
      "name": "<artifact name>",
      "version": "<picolet runtime tag>"
    }
  },
  "components": [
    {
      "type": "library",
      "name": "MicroPython",
      "version": "1.24.0",
      "licenses": [{"license": {"id": "MIT"}}],
      "externalReferences": [{"type": "website", "url": "https://..."}],
      "properties": [
        {"name": "picolet:link_type", "value": "static"},
        {"name": "picolet:variant", "value": "cli,webview,lvgl"}
      ]
    }
  ]
}
```

`type` values used: `library` for all third-party C/Python components;
`framework` for MicroPython. `purl` is included when present in
`runtime.toml`.

For the app SBOM (FR-SBOM-2), the component list is the union of:
1. Components from the runtime's `.cdx.json` (read and de-serialised).
2. Components declared in `[dependencies]` in `picolet.toml`. For v1,
   `[dependencies]` is a TOML table mapping `name = version` (no
   registry resolution yet; the user provides the SPDX licence in a
   sibling `[dependency_meta.<name>]` table if needed, or the generator
   emits `LicenseRef-Unknown` and applies the `warn_unknown` / `fail_unknown`
   policy).
3. `micropython-lib` modules frozen via the manifest. In v1 these are
   declared by the user in `[dependencies]` rather than auto-discovered
   from the manifest file (manifest parsing adds complexity and is
   deferred). A `[PH13] Caveat:` note records the deferral.

The `serialNumber` is a fresh `uuid.uuid4()` on every emission.
Timestamps are `datetime.utcnow().isoformat() + "Z"`. Both are
intentional: the SBOM is a statement of the build at a point in time,
not a reproducible artifact.

#### AD4 — App-side `[sbom]` allowlist enforcement (FR-SBOM-3)

`validator.py` currently has `_SBOM_SCHEMA: dict = {}` (no typed keys).
This phase fills it in:

```python
_SBOM_SCHEMA = {
    "allow_licences": list,   # list of SPDX ids for static and dynamic
    "allow_dynamic":  list,   # additional SPDX ids allowed for dynamic links only
    "warn_unknown":   bool,
    "fail_unknown":   bool,
}
```

Default enforcement (when `[sbom]` is absent or keys are missing):

```
allow_licences = ["MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0",
                  "Zlib", "0BSD", "ISC", "Python-2.0"]
allow_dynamic  = ["LGPL-2.1-or-later", "LicenseRef-MS-WebView2-Fixed"]
warn_unknown   = true
fail_unknown   = false
```

The `warn_unknown` / `fail_unknown` pair covers components whose SBOM
entry carries `LicenseRef-Unknown`. They are independent flags (both
true = warn and fail; only `fail_unknown` true without `warn_unknown`
is treated as both true — fail implies warn).

Enforcement logic in `sbom_gen.emit_app_sbom`:

1. For each component in the merged component list:
   a. If `link_type == "static"`: the licence must be in
      `allow_licences`. Violation if not.
   b. If `link_type == "dynamic"`: the licence must be in
      `allow_licences` OR in `allow_dynamic`. Violation if not.
   c. If licence is `LicenseRef-Unknown`: apply `warn_unknown` /
      `fail_unknown` policy.
2. `SbomViolation` records are returned to `build_cmd.py`.
3. `build_cmd.py` prints warnings for `severity="warn"` entries and
   exits 1 for any `severity="fail"` entry. The SBOM file is always
   written (even on policy failure) so downstream tools can inspect it.

#### AD5 — Runtime SBOM emission in the build pipeline

`build-runtime.sh` gains a post-build step that calls the generator:

```bash
python -m picolet.sbom_gen emit-runtime \
    --output "$BUILD_DIR/$ARTIFACT_NAME.cdx.json" \
    --target "$TARGET" \
    --variant "$VARIANT" \
    --repo-root "$PICOLET_RUNTIME_ROOT/.."
```

This runs inside the build container where `picolet-cli` is available
(it is already used for mpy-cross invocation via the build pipeline).
The `.cdx.json` sidecar lands alongside the runtime binary in
`packages/picolet-runtime/build/`. The existing `runtime_resolver.py`
already fetches and caches a `.cdx.json` sidecar per artifact
(lines 283–356 of `runtime_resolver.py`); PH15's release pipeline
uploads the `.cdx.json` to GitHub Releases, and the resolver's
existing `sbom_url` / `sbom_path` logic then serves the cached copy
to `emit_app_sbom` automatically.

`build_cmd.py` adds a step 10 after the existing step 9:

```python
# Step 10 – Emit SBOM (FR-SBOM-1, FR-SBOM-2, FR-SBOM-3).
sbom_path = output_path.parent / f"{output_path.name}.cdx.json"
violations = emit_app_sbom(
    output_path=sbom_path,
    runtime_sbom_path=resolved.sbom,   # already used as a hook point
    app_data=data,
    target=target,
    variant=variant,
    repo_root=_find_repo_root(),
)
_handle_sbom_violations(violations, data, args.verbose)
```

`resolved.sbom` is the `Path | None` already returned by
`resolve_runtime`; it is `None` during development before PH15 uploads
sidecars. When `None`, the generator uses only `runtime.toml` as the
runtime component source (skipping the merge step for the downloaded
SBOM), which is correct for locally-built runtimes.

#### AD6 — PH15 consumption

PH15's `release.yml` build matrix calls `build-runtime.sh` per artifact
(as it will anyway). After each build, the `.cdx.json` sidecar is
already present alongside the binary. The release workflow uploads both
with `gh release upload` in a single step:

```yaml
- name: Upload artifacts
  run: |
    gh release upload $TAG \
      build/$ARTIFACT \
      build/$ARTIFACT.sha256 \
      build/$ARTIFACT.cdx.json
```

No additional PH15 logic is required beyond this. The resolver's
existing `sbom_url` fetch path (runtime_resolver.py line 285) then
populates the cache on user machines automatically.

### Deliverables

1. `packages/picolet-runtime/sbom/runtime.toml` — hand-curated component
   declarations for all native dependencies across all six variants.
2. `packages/picolet-cli/picolet/sbom_gen.py` — SBOM generator implementing
   `emit_runtime_sbom`, `emit_app_sbom`, `SbomViolation`, and the
   CLI shim.
3. `packages/picolet-cli/picolet/validator.py` — `_SBOM_SCHEMA` populated
   with typed keys for `allow_licences`, `allow_dynamic`, `warn_unknown`,
   `fail_unknown`.
4. `packages/picolet-cli/picolet/build_cmd.py` — step 10 wired in, calling
   `emit_app_sbom` and `_handle_sbom_violations`.
5. `packages/picolet-runtime/scripts/build-runtime.sh` — post-build step
   calling the generator CLI shim to emit the runtime `.cdx.json`.
6. `tests/phase-13/` — test suite (see Verification section).

### Sequence

All from `/home/anl/picolet` on `dev`.

**1. Log the schema and generator-location decisions.**
```
git commit --allow-empty -s -m "[PH13] Decision: runtime.toml schema + sbom_gen.py in picolet-cli" -m "<body covering AD1 + AD2>"
```

**2. Write `runtime.toml`.**
Create `packages/picolet-runtime/sbom/` directory and
`packages/picolet-runtime/sbom/runtime.toml` with all components in the
table above. Include the WebView2 NuGet pin with the version string
from `_copy_webview2_loader` in `build_cmd.py` (NuGet version
`1.0.2210.55` as shown in the error message).

Commit:
```
git commit -s -m "[PH13] Add runtime.toml with all native component declarations."
```

**3. Populate `_SBOM_SCHEMA` in `validator.py`.**
Update `_SBOM_SCHEMA` and the `[sbom]` validation block in
`validate_toml` to type-check the four new keys. No key is required;
all are optional. Commit:
```
git commit -s -m "[PH13] validator: populate _SBOM_SCHEMA with sbom policy keys."
```

**4. Write `sbom_gen.py`.**
Implement in order:
- `load_runtime_toml(repo_root) -> list[dict]` — parses `runtime.toml`,
  applies `variants`/`targets` filters for the given (target, variant).
- `load_mbm_prs(repo_root) -> list[str]` — reads `mbm.toml`, returns
  PR title strings. Used in the MicroPython component's `notes` field.
- `_to_cdx_component(entry: dict) -> dict` — converts a `runtime.toml`
  entry to a CycloneDX component object.
- `emit_runtime_sbom(...)` — assembles and writes the runtime document.
- `emit_app_sbom(...)` — merges runtime + app deps, enforces policy.
- `SbomViolation` dataclass.
- `__main__` block for the CLI shim (`emit-runtime` subcommand).

Commit:
```
git commit -s -m "[PH13] Add sbom_gen.py: CycloneDX 1.5 emission and policy enforcement."
```

**5. Wire step 10 into `build_cmd.py`.**
Import `emit_app_sbom` from `picolet.sbom_gen`. Add `_handle_sbom_violations`
helper. Wire step 10 after `_append_with_trailer`. Add `--no-sbom` flag
(boolean, default False) for tests that don't need the SBOM side-effect.

Commit:
```
git commit -s -m "[PH13] build_cmd: emit SBOM sibling on every picolet build (FR-SBOM-1)."
```

**6. Wire the runtime build script.**
Add the `python -m picolet.sbom_gen emit-runtime ...` call after the
existing `finish_artifact` invocation in `build-runtime.sh`. The
`picolet-cli` package must be importable inside the build container — it
already is (the container runs mpremote and mpy-cross via the same
Python environment). If not, the call is deferred to the post-build step
outside the container (developer evaluates).

Commit:
```
git commit -s -m "[PH13] build-runtime.sh: emit .cdx.json sidecar after each artifact build."
```

**7. Write tests and run them.**
See Verification section.

**8. Confirm `picolet build` on the hello-cli fixture emits a `.cdx.json`.**
```
cd /tmp && picolet init testapp13 --template hello-cli && cd testapp13
picolet build --target linux-x64
ls -la target/linux-x64/
# expect: testapp13 and testapp13.cdx.json
python3 -c "import json,sys; d=json.load(open('target/linux-x64/testapp13.cdx.json')); print(d['specVersion'], len(d['components']), 'components')"
# expect: 1.5 <N> components
```

### Exit gate

| # | Condition | Verification |
|---|---|---|
| 1 | `packages/picolet-runtime/sbom/runtime.toml` exists and is valid TOML with at least 8 `[[component]]` entries covering all six runtime variants. | `python3 -c "import tomllib; d=tomllib.load(open('packages/picolet-runtime/sbom/runtime.toml','rb')); assert len(d['component'])>=8, len(d['component'])"` |
| 2 | **FR-SBOM-1** (runtime SBOM): `build-runtime.sh --target linux-x64 --variant cli` produces both the binary and a `.cdx.json` sidecar. | `ls packages/picolet-runtime/build/picolet-runtime-linux-x64-cli.cdx.json` — file present. |
| 3 | The runtime `.cdx.json` is valid CycloneDX 1.5 JSON with `bomFormat`, `specVersion == "1.5"`, and at least one component. | `python3 -c "import json; d=json.load(open('packages/picolet-runtime/build/picolet-runtime-linux-x64-cli.cdx.json')); assert d['bomFormat']=='CycloneDX' and d['specVersion']=='1.5' and d['components']"` |
| 4 | **FR-SBOM-1** (app SBOM): `picolet build` produces `<app>.cdx.json` alongside the binary in `target/<target>/`. | `ls target/linux-x64/hello-cli13.cdx.json` — file present after build of test fixture. |
| 5 | **FR-SBOM-2**: App SBOM component list is a superset of the runtime SBOM component list. | `python3 tests/phase-13/test_sbom_union.py` — asserts every component `name` in the runtime SBOM also appears in the app SBOM. |
| 6 | **FR-SBOM-3** (allow_licences): a `picolet build` against a fixture with `[sbom] allow_licences = ["MIT"]` prints a warning for the WebView2 webview/windows component (licence `LicenseRef-MS-WebView2-Fixed`) but exits 0 (warn_unknown default). | `picolet build --target linux-x64 --variant cli` with test fixture; grep stderr for `warn:` and assert exit 0. For the warn path: `cd tests/phase-13/fixtures/strict-sbom-warn && picolet build --target linux-x64` — stderr contains `warn:` and exit code is 0. |
| 7 | **FR-SBOM-3** (fail_unknown): a `picolet build` against a fixture with `[sbom] fail_unknown = true` exits 1 when a component has an unknown licence. | `cd tests/phase-13/fixtures/strict-sbom-fail && picolet build --target linux-x64` — exit code is 1 and stderr contains `error: sbom policy`. |
| 8 | `validator.py` rejects invalid types for `[sbom]` keys. | `python3 -m pytest tests/phase-13/test_validator_sbom.py -q` — passes. |
| 9 | The app SBOM `serialNumber` is a valid URN UUID4. | `python3 -c "import json,re; d=json.load(open('target/linux-x64/hello-cli13.cdx.json')); assert re.match(r'urn:uuid:[0-9a-f-]{36}', d['serialNumber'])"` |
| 10 | MicroPython PR list from `mbm.toml` appears in the MicroPython component's notes in the runtime SBOM. | `python3 -c "import json; d=json.load(open('packages/picolet-runtime/build/picolet-runtime-linux-x64-cli.cdx.json')); mp=[c for c in d['components'] if c['name']=='MicroPython'][0]; assert 'pr/' in mp.get('description','') or any('pr/' in str(p) for p in mp.get('properties',[]))"` |
| 11 | `emit_runtime_sbom` filters components correctly by `variants` and `targets`: the cli SBOM does not contain LVGL or SDL2. | `python3 -c "import json; d=json.load(open('packages/picolet-runtime/build/picolet-runtime-linux-x64-cli.cdx.json')); names=[c['name'] for c in d['components']]; assert 'LVGL' not in names and 'SDL2' not in names, names"` |
| 12 | Non-regression: `picolet build` for hello-cli (PH03 fixture) still works with the new step 10 wired in. | `bash tests/phase-03/run.sh` exits 0. |

Gates 2–3 close FR-SBOM-1 (runtime half). Gate 4 closes FR-SBOM-1
(app half). Gate 5 closes FR-SBOM-2. Gates 6–7 close FR-SBOM-3.
Gate 12 is a non-regression guard.

### Verification commands

```bash
# Lint / unit tests
cd /home/anl/picolet
python3 -m pytest tests/phase-13/ -q

# Integration: build runtime + check sidecar
./packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant cli
ls -la packages/picolet-runtime/build/picolet-runtime-linux-x64-cli.cdx.json
python3 -c "
import json
d = json.load(open('packages/picolet-runtime/build/picolet-runtime-linux-x64-cli.cdx.json'))
print('specVersion:', d['specVersion'])
print('components:', len(d['components']))
for c in d['components']:
    print(' ', c['name'], c['version'], [l['license']['id'] for l in c.get('licenses',[])])
"

# Integration: picolet build emits app SBOM
cd /tmp && picolet init hello-cli13 --template hello-cli && cd hello-cli13
picolet build --target linux-x64
python3 -c "
import json
d = json.load(open('target/linux-x64/hello-cli13.cdx.json'))
print('specVersion:', d['specVersion'])
print('components:', len(d['components']))
"

# Policy enforcement — warn path
cd /home/anl/picolet/tests/phase-13/fixtures/strict-sbom-warn
picolet build --target linux-x64 2>&1 | grep -i warn
echo "Exit: $?"

# Policy enforcement — fail path
cd /home/anl/picolet/tests/phase-13/fixtures/strict-sbom-fail
picolet build --target linux-x64; echo "Exit: $?"
```

### Foreseeable risks

**Risk 1: picolet-cli not importable inside the build container for
the runtime SBOM emission step.**

`build-runtime.sh` runs inside a Docker container where mpremote and
mpy-cross are available, but `picolet-cli` may not be on `PYTHONPATH`.
The generator's CLI shim requires `python -m picolet.sbom_gen` to work
inside the container.

Mitigation options (developer evaluates in sequence):
1. The container's entrypoint already sets `PYTHONPATH` to include
   `packages/picolet-cli` (check first).
2. Add `PYTHONPATH=$(pwd)/packages/picolet-cli` to the `python -m
   picolet.sbom_gen` call in `build-runtime.sh`.
3. Move the runtime SBOM emission step out of the container invocation
   entirely: have the build script call the generator from the *host*
   Python after the container exits. The container mounts the repo
   read-write and the artifact is already written to `build/`; the
   host call is therefore `python3 -m picolet.sbom_gen emit-runtime ...`
   on the host shell. This is the cleanest fallback and does not
   require any container changes.

Log the decision as a `[PH13] Decision:` commit.

**Risk 2: `runtime.toml` component versions diverge from what was
actually built.**

`runtime.toml` is hand-maintained. Version strings like `SDL2 2.0.20`
are correct for Linux/Ubuntu 22.04 but the from-source Windows SDL2
build (PH12) may use a different version tag. The SBOM would then
misrepresent the exact version.

Mitigation: confirm the SDL2 version pulled by the MXE toolchain in
`build-runtime.sh` (it is either a pinned MXE package version or a
from-source checkout). Record that version in `runtime.toml`. Add a
`[PH13] Note:` commit with the exact SDL2 version for the windows
static build. Accept a minor version approximation (`2.x`) if the MXE
version is non-deterministic across rebuilds — SBOM consumers tolerate
approximate versions in open source auditing contexts.

**Risk 3: CycloneDX 1.5 conformance — required vs. optional fields.**

The CycloneDX 1.5 JSON schema has some fields that are technically
required for a fully conformant SBOM (e.g. component `bom-ref` must
be unique within the document; `type` must be a specific enum value).
Without a schema-validation library in the generator, it is possible to
emit a document that fails strict CycloneDX validators used by
enterprise tooling.

Mitigation: the generator test suite includes a gate (gate 3) that
validates the emitted document against the CycloneDX 1.5 JSON schema
using `jsonschema` (already a common Python testing dep). The schema
file is downloaded once and committed to `tests/phase-13/` or fetched
at test time. If `jsonschema` is not available, the test uses the
minimal structural checks in the gates above and adds a `[PH13]
Caveat:` noting the full-schema validation gap.

**Risk 4: `[dependencies]` section not yet in the `picolet.toml` schema.**

FR-SBOM-2 mentions "app `[dependencies]`" but the `picolet.toml` schema
in `validator.py` currently has no `[dependencies]` section. The
validator's `_ALLOWED_SECTIONS` would reject it.

Mitigation: for v1, `[dependencies]` is treated as a v1.1 feature. The
app SBOM (FR-SBOM-2) is satisfied in v1 by the runtime component list
alone; user-added Python deps appear in the SBOM only if the user
declares them in a `[dependencies]` table that PH13 adds as an
**optional, passthrough** section (same pattern as the existing unknown-
key tolerance in `_check_section`). Add `"dependencies"` to
`_ALLOWED_SECTIONS` and a `_DEPENDENCIES_SCHEMA = {}` entry so the
validator accepts but does not yet type-check its contents. Log this
deferral as a `[PH13] Caveat:` commit.

**Risk 5: `resolved.sbom` is None for locally-built runtimes.**

When a developer uses `picolet build --from-source` or uses the in-tree
fallback, `resolved.sbom` is `None` (see `runtime_resolver.py` lines
513–514 and 546). `emit_app_sbom` must handle this gracefully: fall
back to reading `runtime.toml` + `mbm.toml` directly from the repo
rather than merging a pre-built SBOM. This is actually the more
authoritative path for local builds and is already the design described
in AD2.

### Out of scope

- Embedding the SBOM inside the binary itself (deferred `embed = true`
  flag, per `docs/sbom.md`).
- Full micropython-lib manifest parsing to auto-discover frozen modules
  (deferred; user-declared `[dependencies]` covers v1 use cases).
- SPDX 2.3 format output (CycloneDX 1.5 is the v1 target per
  `docs/sbom.md`).
- PyPI licence resolution for user app dependencies (the `[dependencies]`
  licence is either user-declared via `[dependency_meta]` or
  `LicenseRef-Unknown`; no PyPI API calls in v1).
- VEX (vulnerability exploitability exchange) overlays.
- `picolet bundle` installer SBOM (post-v1).
- Code signing of the SBOM document.

### Spec traceability

| Spec id | Gate(s) closing it | Notes |
|---|---|---|
| FR-SBOM-1 | 2, 3, 4 | Runtime artifact `.cdx.json` (gate 2+3); app build `.cdx.json` (gate 4). |
| FR-SBOM-2 | 5, 10, 11 | App SBOM is a superset of runtime SBOM; mbm.toml PRs in MicroPython component; variant+target filtering works. |
| FR-SBOM-3 | 6, 7, 8 | Allowlist enforcement (warn + fail paths); validator types the policy keys. |
| NFR-5 | 1, 11 | `runtime.toml` declares link types; LGPL components (WebKitGTK) are marked `dynamic`; static link set verified free of GPL/AGPL. |
| NFR-6 | 1 | Picolet-authored code (including `WebView2_min.h`) declared MIT in `runtime.toml`. |
| NFR-7 | 2 (first half) | Runtime SBOM emission is wired into `build-runtime.sh`; PH15 uploads the sibling alongside the binary. |

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR-SBOM-{1,2,3}, NFR-{5,6,7} normative text. |
| `/home/anl/picolet/docs/v1-plan.md` §PH13 + §PH15 | Phase scope, deliverables, critical path (PH13 feeds PH15). |
| `/home/anl/picolet/docs/sbom.md` | Component table, format decision (CycloneDX 1.5), LGPL relinking approach, build-time enforcement config shape, open questions. |
| `/home/anl/picolet/CLAUDE.md` | Branch/commit policy, escalation policy, dependency policy (new native deps must enter `runtime.toml`). |
| `/home/anl/picolet/packages/picolet-cli/picolet/build_cmd.py` | Pipeline step numbers; `resolved.sbom` hook point at line 181; `_find_repo_root` helper pattern; `_copy_webview2_loader` NuGet version string. |
| `/home/anl/picolet/packages/picolet-cli/picolet/validator.py` | `_SBOM_SCHEMA = {}` stub at line 54; `_ALLOWED_SECTIONS` at line 21; `_check_section` pattern for adding typed keys. |
| `/home/anl/picolet/packages/picolet-cli/picolet/runtime_resolver.py` | `ResolvedRuntime.sbom` field; `sbom_url` / `sbom_path` download logic (lines 283–356); `_find_repo_root` pattern. |
| `/home/anl/picolet/packages/picolet-runtime/mbm.toml` | PR list (7 entries) that feeds the MicroPython component notes. |
| `/home/anl/picolet/docs/phases/PHASE_11_lvgl-renderer-linux.md` | Phase file format and section structure reference. |
