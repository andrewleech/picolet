#!/usr/bin/env bash
# rebuild-integration.sh — rebuild the picolet-runtime micropython submodule's
# integration branch from the PRs listed in mbm.toml.
#
# All Picolet-authored downstream code lives out-of-tree under
# packages/picolet-runtime/{variants,user_c_modules,lib}/.  The integration
# branch tip is composed entirely from the feature branches listed in
# mbm.toml; no post-rebase file copying is performed.
#
# Run from anywhere; resolves repo root from the script location.
#
# After running, commit the submodule pointer in the parent repo:
#   git -C "$(git rev-parse --show-toplevel)" add packages/picolet-runtime/micropython && git commit -s

set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMODULE="$PKG_ROOT/micropython"
RERERE_SRC="$PKG_ROOT/rerere"

cd "$PKG_ROOT"

if [ ! -d "$SUBMODULE/.git" ] && [ ! -f "$SUBMODULE/.git" ]; then
    echo "error: $SUBMODULE is not initialised as a git submodule." >&2
    echo "       run: git submodule update --init --recursive" >&2
    exit 1
fi

# Enable rerere in the submodule and seed the cache with the recorded
# resolutions from packages/picolet-runtime/rerere/. See that directory's
# README for the conflict catalogue.  Without this seeding, the merge of
# pr/ports-windows-ffi and pr/unix-windows-romfs hits a known Makefile
# conflict that the seed resolves automatically.
echo "[0/2] Enabling rerere and seeding cache from $RERERE_SRC"
git -C "$SUBMODULE" config rerere.enabled true
git -C "$SUBMODULE" config rerere.autoUpdate true

# Set local user.email/user.name in the submodule so `git merge` can sign
# the merge commits below.  CI runners (e.g. GitHub Actions) start with no
# global git config; without these, `git merge` fails with:
#   fatal: empty ident name (for <runner@...>) not allowed
# Use a Picolet-CI identity rather than inheriting whatever the developer
# has globally — keeps the integration tip deterministic across machines.
git -C "$SUBMODULE" config user.email "rebuild-integration@picolet.local"
git -C "$SUBMODULE" config user.name "Picolet rebuild-integration"
SUBMODULE_GITDIR="$(git -C "$SUBMODULE" rev-parse --git-dir)"
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

# Ensure the upstream remote exists so PR refs resolve.
if ! git -C "$SUBMODULE" remote | grep -q '^upstream$'; then
    echo "[0/2] Adding upstream remote (https://github.com/andrewleech/micropython.git)"
    git -C "$SUBMODULE" remote add upstream https://github.com/andrewleech/micropython.git
    git -C "$SUBMODULE" fetch upstream
fi

# Ensure PR refs are configured on origin.
if ! git -C "$SUBMODULE" config --get remote.origin.fetch | grep -q 'pull.*head'; then
    echo "[0/2] Configuring origin to fetch PR refs"
    git -C "$SUBMODULE" config --add remote.origin.fetch '+refs/pull/*/head:refs/remotes/origin/pr/*'
    git -C "$SUBMODULE" fetch origin
fi

# Sync local branches to origin's current tip for every branch listed in
# mbm.toml. Force-updated every run, not created-if-missing: mbm.toml's own
# branches are meant to always track whatever origin currently has (PR
# authors rebase/force-push while a PR is open), so a stale local branch
# left over from an earlier run or an unrelated local checkout must never be
# allowed to silently shadow origin's current content.
echo "[0/2] Syncing local branches to origin for the branches listed in mbm.toml"
mapfile -t ALL_BRANCHES < <(grep -E '^name = "' "$PKG_ROOT/mbm.toml" | sed 's/^name = "//;s/"$//')
for pr_branch in "${ALL_BRANCHES[@]}"; do
    git -C "$SUBMODULE" fetch origin --quiet "$pr_branch" 2>/dev/null || true
    if git -C "$SUBMODULE" show-ref --quiet "refs/remotes/origin/$pr_branch"; then
        git -C "$SUBMODULE" branch -f "$pr_branch" "origin/$pr_branch" 2>/dev/null || true
    fi
done

# Bootstrap integration branch if missing.
if ! git -C "$SUBMODULE" show-ref --quiet refs/heads/integration; then
    echo "[0/2] Bootstrapping integration branch from upstream/master"
    git -C "$SUBMODULE" branch integration upstream/master
fi

# Reset to upstream/master before the submodule walk.
git -C "$SUBMODULE" checkout upstream/master --quiet

# Refresh transitive submodules to the pointers recorded by upstream/master.
# Do NOT `submodule deinit` first: that removes each submodule's working tree
# wholesale, including generated-but-untracked files (notably libffi's autogen'd
# `configure`), which forces a cold re-bootstrap needing host autotools on every
# rebuild.  `submodule update` alone moves each submodule to its recorded SHA and
# leaves those warm artifacts in place.
echo "[0/2] Updating transitive submodules"
git -C "$SUBMODULE" submodule update --init --recursive

# Several PRs (notably #38) bump lib/micropython-lib's pointer to a commit
# that only exists on andrewleech's fork. Must happen before the merge loop
# below, not after: pr/lib-pyusb-windows merges that pointer bump, and a
# submodule merge needs the target commit fetchable at merge time -- a
# machine that has never fetched this remote hits "Failed to merge
# submodule lib/micropython-lib (commits not present)" on the very first
# merge otherwise (only invisible on a machine that already had this
# remote warm from an earlier run).
MPL_DIR="$SUBMODULE/lib/micropython-lib"
if [ -d "$MPL_DIR" ]; then
    if ! git -C "$MPL_DIR" remote | grep -q '^andrewleech$'; then
        echo "    adding andrewleech remote on lib/micropython-lib"
        git -C "$MPL_DIR" remote add andrewleech https://github.com/andrewleech/micropython-lib.git
    fi
    git -C "$MPL_DIR" fetch andrewleech --quiet
fi

if ! git -C "$SUBMODULE" diff-index --quiet HEAD --; then
    echo "error: $SUBMODULE still has uncommitted changes after submodule update" >&2
    git -C "$SUBMODULE" status
    exit 1
fi

echo "[1/2] Compose integration_update from mbm.toml PR branches"
#
# Driven directly rather than via `mbm rebase` because mbm 2.0.2's
# `git.merge()` raises on the non-zero exit code from `git merge` even
# when rerere has already auto-resolved + staged the conflict.  mbm.toml
# remains the source of truth for which PRs feed integration; we just
# drive the merges directly with rerere set up correctly.

git -C "$SUBMODULE" checkout -B integration_update upstream/master

# Read all branch names from mbm.toml, not just those prefixed with `pr/`.
# Branches like `manifest_c_module` (upstream micropython PRs we carry as
# named branches on our fork) don't fit the `pr/...` convention but still
# need to be merged into integration.
mapfile -t PR_BRANCHES < <(grep -E '^name = "' "$PKG_ROOT/mbm.toml" | sed 's/^name = "//;s/"$//')

for branch in "${PR_BRANCHES[@]}"; do
    msg="Merge branch '$branch'"
    echo "    merging $branch"
    if git -C "$SUBMODULE" merge --no-ff -m "$msg" "$branch" 2>&1 | sed 's/^/      /' ; then
        merge_rc=${PIPESTATUS[0]}
    else
        merge_rc=${PIPESTATUS[0]}
    fi
    if [ "$merge_rc" -eq 0 ]; then
        continue
    fi
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

echo "[2/2] Promote integration_update -> integration"
git -C "$SUBMODULE" branch -f integration integration_update
git -C "$SUBMODULE" checkout integration
git -C "$SUBMODULE" submodule update --init --recursive

echo
echo "Integration rebuilt at $(git -C "$SUBMODULE" rev-parse --short integration)"
echo "Update parent submodule pointer with:"
echo "  git add packages/picolet-runtime/micropython && \\"
echo "    git commit -s -m 'picolet-runtime: Rebuild integration.'"
