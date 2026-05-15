# Build environment for picolet-runtime linux-x64 artifacts.
#
# Based on ubuntu:22.04 (glibc 2.35) to pin the minimum GLIBC version
# of the produced binary to what Ubuntu 22.04 LTS ships.  Building on a
# newer host (Ubuntu 24.04 / glibc 2.39) silently produces binaries
# with GLIBC_2.38 symbol requirements that break on 22.04.
#
# Tagged locally as: picolet-linux-x64-build:22.04

FROM ubuntu:22.04

# Avoid interactive prompts during apt-get.
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        python3 \
        git \
        autoconf \
        automake \
        libtool \
        libwebkit2gtk-4.1-0 \
        xvfb \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PH07: libwebkit2gtk-4.1-0 + xvfb are runtime-only dependencies for
# the webview variant.  We do NOT install libwebkit2gtk-4.1-dev — the
# runtime dlopens webkit2gtk-4.1 via libffi at startup, so headers are
# never needed at build time (NFR-5: no GPL/LGPL static link).  xvfb is
# only used by the test harness on a headless host; NFR-8 carve-out.
