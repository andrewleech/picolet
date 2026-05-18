# Running unsigned Picolet binaries on macOS

Picolet v1.2 ships unsigned Mach-O binaries. macOS Gatekeeper quarantines
binaries downloaded from the internet that lack a notarisation ticket.
Code signing and notarisation are deferred to v1.3 (NFR-MAC-6).

## Symptom

When you try to run a downloaded Picolet binary you see one of:

- "picolet-runtime-macos-arm64-cli" cannot be opened because the developer
  cannot be verified.
- "picolet-runtime-macos-arm64-cli" is damaged and can't be opened.

Both are Gatekeeper refusing to run an unsigned/unnotarised binary.

## Workarounds

### Option A — xattr (command line)

Remove the quarantine attribute before running:

```
xattr -d com.apple.quarantine picolet-runtime-macos-arm64-cli
chmod +x picolet-runtime-macos-arm64-cli
./picolet-runtime-macos-arm64-cli
```

This is a one-time operation per binary.

### Option B — right-click Open (Finder)

1. In Finder, right-click (Control-click) the binary.
2. Choose **Open** from the context menu.
3. In the dialog that appears, click **Open** again.

macOS records the override and will not prompt again for this binary.

### Option C — System Settings (macOS 13+)

If macOS blocked execution and you see a notification:

1. Open **System Settings → Privacy & Security**.
2. Scroll to the **Security** section.
3. Click **Open Anyway** next to the blocked binary name.

## Why this happens

Apple's Gatekeeper checks every application launched from the internet
for a Developer ID signature and a notarisation ticket from Apple's
servers. Picolet v1.2 binaries are unsigned, so Gatekeeper blocks them by
default.

The quarantine attribute (`com.apple.quarantine`) is set by the browser
or download tool when the file arrives. Removing it (xattr -d) or
allowing it through System Settings tells Gatekeeper you trust this
specific file.

## Roadmap

Code signing and notarisation are planned for v1.3. Once signed and
notarised, users will be able to run Picolet binaries on macOS without
any workaround.
