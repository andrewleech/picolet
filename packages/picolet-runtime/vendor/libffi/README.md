# Vendored libffi autogen output

This directory mirrors `autoreconf`'s complete output for the `lib/libffi`
submodule — `configure` plus every generated file it needs alongside it at
build time (`ltmain.sh`, `compile`, `install-sh`, `missing`, `depcomp`, the
`Makefile.in` templates, the `m4/*.m4` libtool macros, `doc/mdate-sh`,
`doc/texinfo.tex`, `aclocal.m4`, `fficonfig.h.in`). `configure` alone is not
sufficient: without its companions it fails with "cannot find required
auxiliary files: ltmain.sh compile missing install-sh".

Generated on a host with a working autotools toolchain (libtool 2.4.7+,
which provides the `LT_SYS_SYMBOL_USCORE` m4 macro libffi's `configure.ac`
needs). Vendored here because `build-runtime.sh`'s own host-autogen fallback
is unreliable across environments — it can depend on exactly which
`automake`/`aclocal` a given host resolves first in `PATH`, not just on the
advertised package versions being "new enough". Confirmed the hard way: a
plain `ubuntu:24.04` container with `libtool`/`automake`/`autoconf`/`texinfo`
installed generates a working `configure`, while a GitHub Actions
`ubuntu-latest` runner with the identical package list still hit
`configure.ac:219: error: possibly undefined macro: LT_SYS_SYMBOL_USCORE`.

`build-runtime.sh` copies this whole tree into `lib/libffi/` before falling
back to running `autogen.sh` on the host, so a fresh CI runner or any other
machine's autotools quirks never matter for a cold libffi build.

## Regenerating

Only needed if `lib/libffi`'s own `configure.ac`/`Makefile.am`/`m4/*.m4`
change (a libffi submodule bump). On a host where `./autogen.sh` already
works (verify with `libtoolize --version` >= 2.4.7):

```bash
cd packages/picolet-runtime/micropython/lib/libffi
rm -f configure
./autogen.sh

VENDOR=../../../vendor/libffi
rm -rf "$VENDOR"
mkdir -p "$VENDOR"
for f in Makefile.in aclocal.m4 compile configure depcomp doc/Makefile.in \
         doc/mdate-sh doc/texinfo.tex fficonfig.h.in include/Makefile.in \
         install-sh ltmain.sh m4/libtool.m4 m4/ltoptions.m4 m4/ltsugar.m4 \
         m4/ltversion.m4 'm4/lt~obsolete.m4' man/Makefile.in missing \
         testsuite/Makefile.in; do
    mkdir -p "$(dirname "$VENDOR/$f")"
    cp -p "$f" "$VENDOR/$f"
done
```

`git status --short --ignored` inside `lib/libffi` after `autogen.sh` lists
the authoritative generated-file set (everything `!!`-marked except
`autom4te.cache/`, which is a pure regeneration-speed cache for `autoreconf`
itself — not needed by `configure` at build time, so it isn't vendored).

Generated from `lib/libffi` at commit `3d0ce1e6fcf19f853894862abcbac0ae78a7be60`.
