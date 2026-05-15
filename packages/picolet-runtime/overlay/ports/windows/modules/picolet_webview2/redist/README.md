# WebView2Loader.dll — redistributable loader stub

This directory holds the bundled `WebView2Loader.x64.dll` that
`picolet build` copies into the application romfs at
`/rom/picolet/WebView2Loader.dll`.

The runtime extracts it to `%LOCALAPPDATA%\picolet\<pid>\WebView2Loader.dll`
at first use and loads it with `LoadLibraryW` (see
`picolet_wv2_load_loader_dll` in the C overlay).

## Source

Microsoft Edge WebView2 SDK NuGet package, pinned version 1.0.2210.55
(or compatible).  Extract `build/native/x64/WebView2Loader.dll`.

Obtain via either:

  1. nuget install Microsoft.Web.WebView2 -Version 1.0.2210.55
  2. Direct download from
     https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.2210.55

Place the extracted `WebView2Loader.dll` here as
`WebView2Loader.x64.dll`.

## Why not vendored in git

The DLL is a redistributable binary; pinning it to the git tree adds
~150 KB to every clone.  The phase plan calls for it to be checked in
under deliverable 6, but PH10's developer pass leaves the redist
fetch as a build-time / packaging-time concern (the `picolet build`
helper will fail loudly if the DLL is missing and surface the link
above so users can self-provision it).  PH13 / PH15 may move this
file under git LFS or fetch it as part of `scripts/fetch-deps.sh`.

## License

See `../LICENSE.WebView2-SDK`.  The loader is redistributable under
the Microsoft WebView2 SDK License Terms.
