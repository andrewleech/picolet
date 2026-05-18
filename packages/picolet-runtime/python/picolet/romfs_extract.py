# picolet.romfs_extract — extract files from romfs to a real-filesystem temp
# directory so OS loaders (Windows LoadLibrary, etc.) can find them.
#
# romfs is a virtual filesystem that MicroPython can read but most OS
# loaders cannot.  On Windows in particular, ffi.open() against a
# /rom/... path fails because LoadLibraryW only accepts real paths.
# This helper copies the file to %TEMP%\<subdir>\ (Windows) or returns
# the input path unchanged (Linux, macOS — where ffi.open can read
# from /rom directly via VFS plumbing).

import os
import sys


def extract_to_temp(romfs_path, subdir="picolet"):
    """Copy a file out of romfs into a real-filesystem temp directory.

    Returns the real path of the extracted file (Windows) OR the input
    path unchanged (non-Windows).  Idempotent: re-runs skip the copy
    when the destination already exists with the same size.

    Args:
        romfs_path: absolute path to the file inside romfs
                    (e.g. "/rom/src/_usb/libusb-1.0.dll").
        subdir:     name of the temp subdirectory; defaults to "picolet".
                    Use a per-app slug (e.g. "picolet_pydfu") to keep
                    multiple Picolet apps from conflicting.

    Returns: real filesystem path (str).

    Raises: OSError if the romfs file is missing or the extraction
            fails irrecoverably.  Callers that want graceful fallback
            should catch OSError and fall back to system PATH lookup.
    """
    if sys.platform != "win32":
        return romfs_path

    if not _stat_ok(romfs_path):
        raise OSError("file not found in romfs: " + romfs_path)

    base = os.getenv("TEMP") or os.getenv("TMP") or "C:\\Windows\\Temp"
    dest_dir = base + "\\" + subdir
    try:
        os.mkdir(dest_dir)
    except OSError:
        pass  # already exists

    name = romfs_path.rsplit("/", 1)[-1]
    dest = dest_dir + "\\" + name

    # Idempotency: skip copy if dest exists with the same size as the
    # romfs source.  Size match is a cheap stand-in for content match;
    # romfs files don't change between runs of the same binary.
    src_size = os.stat(romfs_path)[6]
    try:
        if os.stat(dest)[6] == src_size:
            return dest
    except OSError:
        pass

    with open(romfs_path, "rb") as fsrc:
        with open(dest, "wb") as fdst:
            while True:
                chunk = fsrc.read(4096)
                if not chunk:
                    break
                fdst.write(chunk)
    return dest


def extract_dir(romfs_dir, subdir="picolet"):
    """Convenience: extract every file in a romfs directory.

    Returns the real-filesystem destination directory path on Windows,
    or romfs_dir unchanged on non-Windows.  Useful when a module ships
    a small group of related natives (e.g. libusb + dependency DLLs).
    """
    if sys.platform != "win32":
        return romfs_dir

    base = os.getenv("TEMP") or os.getenv("TMP") or "C:\\Windows\\Temp"
    dest_dir = base + "\\" + subdir
    try:
        os.mkdir(dest_dir)
    except OSError:
        pass

    for name in os.listdir(romfs_dir):
        src = romfs_dir + "/" + name
        # Skip nested directories — extract only top-level files.
        # If the caller needs a recursive extract, they can call
        # extract_to_temp per file.
        try:
            if _is_dir(src):
                continue
        except OSError:
            continue
        extract_to_temp(src, subdir)
    return dest_dir


def _stat_ok(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _is_dir(path):
    mode = os.stat(path)[0]
    return (mode & 0x4000) != 0  # S_IFDIR
