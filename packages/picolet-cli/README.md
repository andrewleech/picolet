# picolet-cli

The `picolet` command-line tool.

```
picolet init <name>           # scaffold an app from a template
picolet dev                   # iterative loop: rebuild + relaunch on change
picolet build [--target X]    # produce a binary in target/<target>/
picolet build --from-source   # rebuild the runtime from source via dockcross
picolet run                   # build (if needed) and run the binary
```

Implemented as a uv-runnable Python entry point with
[PEP 723](https://peps.python.org/pep-0723/) inline dependencies. Not yet
implemented.

## Build pipeline

For each `picolet build`:

1. Read app-level `picolet.toml`, resolve `[ui] renderer` → runtime variant.
2. Download the matching `picolet-runtime-{target}-{variant}` artifact from
   the Picolet release (cached under `.picolet-cache/`).
3. Compile the app's Python sources (`src/`) to `.mpy` via `mpy-cross`.
4. Build a romfs image from `[romfs] include` directories + frozen
   manifest.
5. Append the romfs to the runtime binary (the runtime's vfs_rom_ioctl
   reads its image from a known offset).
6. Apply `[app]` metadata (name, icon, version) by patching the binary's
   resources where the platform supports it.
7. Emit `target/<target>/<app-name>[.exe]`.
