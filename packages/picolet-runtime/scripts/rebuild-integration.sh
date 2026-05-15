#!/usr/bin/env bash
# rebuild-integration.sh — rebuild the picolet-runtime micropython submodule's
# integration branch from the PRs listed in mbm.toml, then re-apply the
# picolet overlay (renderer modules + port variants) on top.
#
# Run from anywhere; resolves repo root from the script location.
#
# After running, commit the submodule pointer in the parent repo:
#   git -C "$(git rev-parse --show-toplevel)" add packages/picolet-runtime/micropython && git commit -s

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMODULE="$PKG_ROOT/micropython"
OVERLAY="$PKG_ROOT/overlay"

cd "$PKG_ROOT"

if [ ! -d "$SUBMODULE/.git" ] && [ ! -f "$SUBMODULE/.git" ]; then
    echo "error: $SUBMODULE is not initialised as a git submodule." >&2
    echo "       run: git submodule update --init --recursive" >&2
    exit 1
fi

# Ensure the upstream remote exists so `mbm rebase --target upstream/master`
# resolves on a fresh clone. We configure it to point to the andrewleech fork
# where the PR branches exist, so mbm can find them when rebasing.
# Both the fork and the main repo have synchronized masters, so rebasing onto
# upstream/master here will be equivalent to rebasing onto the main micropython
# master.
if ! git -C "$SUBMODULE" remote | grep -q '^upstream$'; then
    echo "[0/3] Adding upstream remote (https://github.com/andrewleech/micropython.git)"
    git -C "$SUBMODULE" remote add upstream https://github.com/andrewleech/micropython.git
    git -C "$SUBMODULE" fetch upstream
fi

# Ensure PR refs are available from origin (andrewleech/micropython fork).
# mbm will fetch PR branches when it runs, but we need to ensure origin
# is configured to fetch them.
if ! git -C "$SUBMODULE" config --get remote.origin.fetch | grep -q 'pull.*head'; then
    echo "[0/3] Configuring origin to fetch PR refs"
    git -C "$SUBMODULE" config --add remote.origin.fetch '+refs/pull/*/head:refs/remotes/origin/pr/*'
    git -C "$SUBMODULE" fetch origin
fi

# Pre-create local branches for each PR so mbm can find them. mbm expects
# branches named pr/lib-pyusb-windows, pr/gc-add-heap, etc. to exist locally.
echo "[0/3] Creating local branches for the seven PRs"
for pr_branch in pr/lib-pyusb-windows pr/mkrules-exe-fix pr/mkrules-frozen-str pr/gc-add-heap pr/ports-windows-ffi pr/unix-windows-romfs pr/ports-windows-variant-overrides; do
    if ! git -C "$SUBMODULE" show-ref --quiet "refs/heads/$pr_branch"; then
        # Extract PR number from branch name (e.g., "pr/gc-add-heap" -> 41)
        pr_num=$(git -C "$SUBMODULE" for-each-ref "refs/remotes/origin/$pr_branch" --format='%(refname)' | grep -oE 'pr/[0-9]+$' || echo "$pr_branch")
        git -C "$SUBMODULE" branch "$pr_branch" "origin/$pr_branch" 2>/dev/null || true
    fi
done

# Ensure submodule state is clean (update any nested submodules)
echo "[0/3] Updating transitive submodules"
git -C "$SUBMODULE" submodule deinit -f .
git -C "$SUBMODULE" submodule update --init --recursive

# Verify the working tree is truly clean before calling mbm
if ! git -C "$SUBMODULE" diff-index --quiet HEAD --; then
    echo "error: $SUBMODULE still has uncommitted changes after submodule update" >&2
    git -C "$SUBMODULE" status
    exit 1
fi

echo "[1/3] mbm rebase --target upstream/master --local"
mbm rebase --target upstream/master --local --force-push --no-dry-run

echo "[2/3] Promote integration_update -> integration"
git -C "$SUBMODULE" checkout integration
git -C "$SUBMODULE" reset --hard integration_update

echo "[3/3] Apply picolet overlay (variants + native modules)"
if [ -d "$OVERLAY" ] && [ -n "$(ls -A "$OVERLAY" 2>/dev/null)" ]; then
    cd "$OVERLAY"
    find . -type f -print0 | while IFS= read -r -d '' f; do
        dest="$SUBMODULE/${f#./}"
        mkdir -p "$(dirname "$dest")"
        cp "$f" "$dest"
    done
    cd "$PKG_ROOT"

    git -C "$SUBMODULE" add -A
    git -C "$SUBMODULE" -c core.hooksPath=/dev/null commit -s -m "picolet runtime: Apply downstream overlay.

Downstream-only payload for picolet-runtime: renderer-specific MicroPython
port variants and the picolet_webview / picolet_ipc / picolet_window native
modules.

This commit lives only on the local integration branch and is not
intended for upstream PR. Re-applied by scripts/rebuild-integration.sh
after each mbm rebase."
else
    echo "  (overlay directory is empty — skipping)"
fi

echo
echo "Integration rebuilt at $(git -C "$SUBMODULE" rev-parse --short integration)"
echo "Update parent submodule pointer with:"
echo "  git add packages/picolet-runtime/micropython && \\"
echo "    git commit -s -m 'picolet-runtime: Rebuild integration.'"
