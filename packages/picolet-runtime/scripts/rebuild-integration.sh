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
RERERE_SRC="$PKG_ROOT/rerere"

cd "$PKG_ROOT"

if [ ! -d "$SUBMODULE/.git" ] && [ ! -f "$SUBMODULE/.git" ]; then
    echo "error: $SUBMODULE is not initialised as a git submodule." >&2
    echo "       run: git submodule update --init --recursive" >&2
    exit 1
fi

# Enable rerere in the submodule and seed the cache with the recorded
# resolutions from packages/picolet-runtime/rerere/. See that directory's
# README for the conflict catalogue. Without this seeding, mbm will fail
# on the known cross-PR Makefile conflict between pr/ports-windows-ffi
# and pr/unix-windows-romfs.
echo "[0/3] Enabling rerere and seeding cache from $RERERE_SRC"
git -C "$SUBMODULE" config rerere.enabled true
git -C "$SUBMODULE" config rerere.autoUpdate true
SUBMODULE_GITDIR="$(git -C "$SUBMODULE" rev-parse --git-dir)"
# rev-parse --git-dir returns a relative path; resolve to absolute.
case "$SUBMODULE_GITDIR" in
    /*) ;;
    *)  SUBMODULE_GITDIR="$SUBMODULE/$SUBMODULE_GITDIR" ;;
esac
mkdir -p "$SUBMODULE_GITDIR/rr-cache"
if [ -d "$RERERE_SRC" ] && [ -n "$(ls -A "$RERERE_SRC" 2>/dev/null | grep -v '^README' || true)" ]; then
    for entry in "$RERERE_SRC"/*/; do
        [ -d "$entry" ] || continue
        hash="$(basename "$entry")"
        if [ ! -d "$SUBMODULE_GITDIR/rr-cache/$hash" ]; then
            cp -r "$entry" "$SUBMODULE_GITDIR/rr-cache/"
            echo "    seeded rr-cache/$hash"
        fi
    done
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

# Ensure the integration branch exists. mbm rebase fails on a fresh
# submodule because it tries to diff `upstream/master..integration` to
# show the existing state before rebuilding. Bootstrap from
# upstream/master so the first run has somewhere to land.
if ! git -C "$SUBMODULE" show-ref --quiet refs/heads/integration; then
    echo "[0/3] Bootstrapping integration branch from upstream/master"
    git -C "$SUBMODULE" branch integration upstream/master
fi

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

echo "[1/3] Compose integration_update from mbm.toml PR branches"
#
# Why we drive this ourselves instead of `mbm rebase`:
#
# mbm 2.0.2's `git.merge()` raises on the non-zero exit code from
# `git merge` even when rerere has already auto-resolved + staged the
# conflict, and its `_resume_rebase` only knows how to recover from
# in-progress rebases (not in-progress merges). The combination
# silently drops the PR whose merge hit a rerere-resolved conflict.
# See `[PH00] Caveat: mbm rerere handling` commit for the full trace.
#
# mbm.toml remains the source of truth for which PRs feed integration;
# we just drive the merges directly with rerere set up correctly.

git -C "$SUBMODULE" checkout -B integration_update upstream/master

# Parse PR branch names from mbm.toml in declaration order.
mapfile -t PR_BRANCHES < <(grep -E '^name = "pr/' "$PKG_ROOT/mbm.toml" | sed 's/^name = "//;s/"$//')

for branch in "${PR_BRANCHES[@]}"; do
    msg="Merge branch '$branch'"
    echo "    merging $branch"
    if git -C "$SUBMODULE" merge --no-ff -m "$msg" "$branch" 2>&1 | sed 's/^/      /' ; then
        # Check pipefail bit — `set -o pipefail` is not in effect here.
        # PIPESTATUS[0] gives the merge's real exit code.
        merge_rc=${PIPESTATUS[0]}
    else
        merge_rc=${PIPESTATUS[0]}
    fi
    if [ "$merge_rc" -eq 0 ]; then
        continue
    fi
    # Merge non-zero — check whether rerere auto-resolved everything.
    unmerged_count=$(git -C "$SUBMODULE" ls-files --unmerged | wc -l)
    if [ "$unmerged_count" -eq 0 ] && [ -f "$SUBMODULE_GITDIR/MERGE_HEAD" ]; then
        echo "      rerere auto-resolved; finalising merge commit"
        git -C "$SUBMODULE" -c core.hooksPath=/dev/null commit --no-edit -s --no-verify >/dev/null
    else
        echo "      error: unresolved conflict in $branch" >&2
        git -C "$SUBMODULE" status >&2
        exit 1
    fi
done

echo "[2/3] Promote integration_update -> integration"
git -C "$SUBMODULE" branch -f integration integration_update
git -C "$SUBMODULE" checkout integration
# Several PRs (notably #38) bump nested-submodule pointers to commits
# that only exist on andrewleech's forks. Ensure the andrewleech remote
# is present on lib/micropython-lib so the desired commit is reachable.
MPL_DIR="$SUBMODULE/lib/micropython-lib"
if [ -d "$MPL_DIR" ]; then
    if ! git -C "$MPL_DIR" remote | grep -q '^andrewleech$'; then
        echo "    adding andrewleech remote on lib/micropython-lib"
        git -C "$MPL_DIR" remote add andrewleech https://github.com/andrewleech/micropython-lib.git
    fi
    git -C "$MPL_DIR" fetch andrewleech --quiet
fi
git -C "$SUBMODULE" submodule update --init --recursive

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
