# Vendored libffi `configure`

`configure` here is `autoreconf`'s output for the `lib/libffi` submodule,
generated on a host with a working autotools toolchain (libtool 2.4.7+,
which provides the `LT_SYS_SYMBOL_USCORE` m4 macro libffi's
`configure.ac` needs). It's the same file `./autogen.sh` would produce
inside `lib/libffi` itself — vendored here because `build-runtime.sh`'s
own host-autogen fallback is unreliable across environments: it can
depend on exactly which `automake`/`aclocal` a given host resolves
first in `PATH`, not just on the advertised package versions being
"new enough". Confirmed the hard way — a plain `ubuntu:24.04` container
with `libtool`/`automake`/`autoconf`/`texinfo` installed generates a
working `configure`, while a GitHub Actions `ubuntu-latest` runner
with the identical package list still hit
`configure.ac:219: error: possibly undefined macro: LT_SYS_SYMBOL_USCORE`.

`build-runtime.sh` copies this into place before falling back to
running `autogen.sh` on the host, so a fresh CI runner or any other
machine's autotools quirks never matter for a cold libffi build.

## Regenerating

Only needed if `lib/libffi`'s own `configure.ac`/`m4/*.m4` change
(a libffi submodule bump). On a host where `./autogen.sh` already
works (verify with `libtoolize --version` >= 2.4.7):

```bash
cd packages/picolet-runtime/micropython/lib/libffi
rm -f configure
./autogen.sh
cp configure ../../../vendor/libffi/configure
chmod +x ../../../vendor/libffi/configure
```

Generated from `lib/libffi` at commit `3d0ce1e6fcf19f853894862abcbac0ae78a7be60`.
