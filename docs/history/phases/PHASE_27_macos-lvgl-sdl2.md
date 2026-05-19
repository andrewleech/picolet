# PHASE 27 — macOS LVGL via SDL2-Cocoa

## Goal

Produce `picolet-runtime-macos-{x64,arm64}-lvgl` using SDL2's native Cocoa
backend. This should be significantly simpler than PH25 — SDL2 abstracts
the Cocoa windowing layer entirely and already supports Darwin natively.
The existing Linux LVGL overlay and manifests require minimal or no
Darwin-specific changes.

## Prerequisites

- PH24 complete (macOS cli runtime builds, native macOS build path proven).
- PH11 and PH12 complete (Linux and Windows LVGL working — establishes
  the pattern).

## Spec coverage

| FR / NFR | Deliverable |
|---|---|
| FR-LV-MAC-1 | macOS lvgl variant links SDL2 via brew (`libSDL2.dylib`) |
| FR-LV-MAC-2 | SDL2 Cocoa backend used by default (no source patch) |
| FR-LV-MAC-3 | Existing Linux LVGL overlay extends to macOS without Darwin fork |
| FR-RT-MAC-1 (lvgl) | Both macos-x64-lvgl and macos-arm64-lvgl artifacts exist |
| FR-EX-MAC-1 (hello-lvgl) | hello-lvgl demo runs on macOS |
| NFR-MAC-3 | lvgl artifact ≤ 2 MiB |
| NFR-MAC-4 | lvgl variant requires only brew sdl2 at runtime |
| NFR-MAC-8 | No GPL/AGPL static link |

## Dependencies

- PH24 (macOS cli builds).
- PH11 (Linux LVGL overlay — precedent).

## Key research findings

### SDL2 on macOS

SDL2 has first-class Cocoa support. The Cocoa video backend is the default
on macOS — no configuration needed. Installing via:
```bash
brew install sdl2
```
provides:
- `/usr/local/lib/libSDL2.dylib` (x64, Homebrew on Intel)
- `/opt/homebrew/lib/libSDL2.dylib` (arm64, Homebrew on Apple Silicon)
- `/usr/local/include/SDL2/` or `/opt/homebrew/include/SDL2/` headers

The SDK headers are needed at compile time; the dylib is needed at runtime.

### Dynamic vs static SDL2 on macOS

On Linux, the LVGL variant links SDL2 dynamically (system `libSDL2.so`).
On Windows, a static from-source SDL2 (with `-ffunction-sections`) was
used to meet the 2 MiB size gate.

On macOS, dynamic linking is the right choice:
- macOS already provides Cocoa frameworks which SDL2 delegates to.
- A static SDL2 on macOS would still need to link Cocoa.framework,
  AppKit.framework, IOKit.framework, etc. dynamically.
- The resulting binary size with dynamic SDL2 should be well under 2 MiB
  for the same reasons as Linux.

NFR-MAC-4 explicitly accepts dynamic SDL2 on macOS (equivalent to Linux
requiring `libwebkit2gtk-4.1-0` for the webview variant).

### lv_binding_micropython on macOS

`lv_binding_micropython` is already initialised as a nested submodule
under `overlay/lib/`. It builds against SDL2 and does not have
platform-specific code for macOS vs Linux at the LVGL layer — SDL2
abstracts the window/event system.

The only build-time difference: SDL2 header and library paths differ
between Linux and macOS. On macOS, pass `-I$(brew --prefix sdl2)/include`
and `-L$(brew --prefix sdl2)/lib` to the compiler.

### `brew --prefix` dynamic lookup

`brew --prefix sdl2` outputs the brew prefix for the sdl2 formula:
- Intel: `/usr/local/opt/sdl2`
- ARM: `/opt/homebrew/opt/sdl2`

This can be evaluated in the `build-runtime.sh` macOS path:
```bash
SDL2_PREFIX="$(brew --prefix sdl2 2>/dev/null || echo /usr/local)"
```

Pass to Make:
```bash
make -j \
    VARIANT="${VARIANT_NAME}" \
    SDL2_INCLUDE_DIR="${SDL2_PREFIX}/include" \
    SDL2_LIB_DIR="${SDL2_PREFIX}/lib" \
    ...
```

### LVGL mpconfigvariant.mk for macOS

The existing
`overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.mk`
points at `lv_binding_micropython` but does not set SDL2 paths (Linux
uses `pkg-config sdl2` in the LVGL binding's cmake). On macOS:
```make
ifeq ($(UNAME_S),Darwin)
SDL2_CFLAGS = -I$(SDL2_INCLUDE_DIR)/SDL2
SDL2_LDFLAGS = -L$(SDL2_LIB_DIR) -lSDL2
else
# Linux: use pkg-config
SDL2_CFLAGS = $(shell pkg-config --cflags sdl2)
SDL2_LDFLAGS = $(shell pkg-config --libs sdl2)
endif
```

## Files to modify

### `packages/picolet-runtime/scripts/build-runtime.sh`

The `build_macos` function (added in PH24) gains a `lvgl` variant path:
- Installs `brew install sdl2` (idempotent).
- Evaluates `SDL2_PREFIX`.
- Passes `SDL2_INCLUDE_DIR` and `SDL2_LIB_DIR` to Make.

### `overlay/ports/unix/variants/picolet-lvgl/mpconfigvariant.mk`

Add Darwin-conditional SDL2 path lookup (see above).

### `sbom/runtime.toml`

Add:
```toml
[[component]]
name = "SDL2"
version = "2.x.x (brew)"
licence = "Zlib"
source_url = "https://www.libsdl.org/"
link_type = "dynamic"
targets = ["macos-x64", "macos-arm64"]
variants = ["lvgl"]
notes = "Installed via brew install sdl2. SDL2 Cocoa backend provides macOS window/event loop."
```

## Integration points

### CI setup for lvgl variant on macOS

In the CI matrix (stub for this phase, full matrix in PH29):
```yaml
- name: Install SDL2 (macOS, lvgl variant only)
  if: matrix.variant == 'lvgl' && startsWith(matrix.target, 'macos-')
  run: brew install sdl2
```

### lv_binding_micropython submodule initialisation

The existing `build-runtime.sh` already has:
```bash
if [[ "$VARIANT" == "lvgl" ]]; then
    local lvbm_dir="$PKG_ROOT/overlay/lib/lv_binding_micropython"
    git -C "$lvbm_dir" submodule update --init --recursive --quiet
fi
```
This path is shared by Linux, Windows, and macOS — no change needed.

## Testing strategy

1. Build `macos-arm64-lvgl` in CI.
2. Run hello-lvgl binary on a macOS arm64 machine:
   ```bash
   xattr -d com.apple.quarantine picolet-runtime-macos-arm64-lvgl
   ./picolet-runtime-macos-arm64-lvgl
   ```
   Expected: LVGL window opens with the hello-lvgl label visible.
3. Check binary size ≤ 2 MiB.
4. Verify dylib dependency: `otool -L picolet-runtime-macos-arm64-lvgl`
   should show `libSDL2.dylib` and system frameworks (Cocoa etc.),
   not a statically linked SDL2.
5. LVGL test via `picolet._test` API (PICOLET_TEST_MODE=1):
   ```python
   async with AppHarness("./picolet-runtime-macos-arm64-lvgl") as h:
       png = await h.snapshot()
       assert len(png) > 1024
   ```

## Success criteria

- [ ] `picolet-runtime-macos-x64-lvgl` and `picolet-runtime-macos-arm64-lvgl`
      build in CI.
- [ ] hello-lvgl window opens on macOS with no crash.
- [ ] Binary size ≤ 2 MiB for both architectures.
- [ ] `otool -L` shows `libSDL2.dylib` as a dynamic dependency.
- [ ] LVGL snapshot returns valid PNG bytes.

## Risks

1. **SDL2 version mismatch**: The brew-installed SDL2 version may differ
   from the version expected by `lv_binding_micropython`. Check the
   binding's SDL2 minimum version requirement. SDL2 2.0.14+ is needed
   for Cocoa backend fixes; brew ships a recent version.

2. **`pkg-config` not available on macOS CI**: The existing Linux LVGL
   path uses `pkg-config sdl2`. macOS runners have pkg-config via brew
   but the SDL2 pkg-config file may not be in the default search path.
   The `SDL2_INCLUDE_DIR`/`SDL2_LIB_DIR` Make variables bypass this.

## Model tier recommendation

planner `sonnet`, developer `sonnet`, sqe `sonnet`, tester `sonnet`.
SDL2 Cocoa backend is mature; this phase is primarily build plumbing.
