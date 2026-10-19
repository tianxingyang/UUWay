# Guarded UU virtual-display size requests

This branch adds a guarded path for UU's reviewed IDD active-mode requests and
distribution checks for Wine installation. It is **not an end-to-end claim of
automatic resizing when a UU controller window changes size**. The native UU
controller remains required; no RDP controller is used as a substitute.

## Request boundary

| Ingress | Meaning and scope |
| --- | --- |
| IDD `0x30c010` | Supplies a bounded mode catalogue; it is not a viewport event. |
| IDD `0x30c00c` on an owned active output | Selects the first supplied mode. This branch routes it through `DisplayRouter` and the rollback guardian. |
| `ChangeDisplaySettingsEx` / ordinary selected-output changes | Retain the existing Win32-to-guardian path. |
| `SetDisplayConfig` / internal `TOPOLOGY_CONTROL` | Retain the existing separate Super Screen topology/exclusive-layout lifecycle. This change does not claim that this entire path now has frame-confirmed rollback. |
| Controller window resize / phone rotation | Requires observation of what the actual UU version emits. No undocumented viewport packet or client event is invented here. |

The Windows adapter in `src/uu_native_virtual_trace_win.h` already emits
`WM_DISPLAYCHANGE` after a successful active-mode request. The display service
now sends that request through the same temporary display transaction used by
ordinary resolution changes, rather than directly negotiating the virtual node.

The metadata-only PipeWire negotiator establishes the requested virtual size.
It cannot confirm that UU is receiving video. The existing capture broker reads
the pending selected-output transaction, retires the old allocation/producer,
and lets UU recreate its Desktop Duplication/encoder session. The confirmation
gate requires the requested dimensions, the selected output, an allocation
started after the latest request, and an owned UU consumer. An allocation ACK
does **not** prove hardware encoding, network delivery, remote decoding, or
presentation.

## Continuous requests and failure handling

Only owned virtual outputs with the `gpu_frame` policy may replace an observed
pending resize. The original rollback baseline survives a burst of requests;
each applied replacement has a new transaction ID and request timestamp.
An old same-size allocation from an A-to-B-to-A sequence cannot confirm A again.
Physical outputs, manual confirmations, ambiguous observations and later local
display changes do not gain this replacement permission.

The previous mode remains available internally for rollback even after a full
64-entry catalogue replaces it or the bounded negotiation history rolls over.
This does not expand the 64-entry Windows monitor catalogue. A checkpoint failure
before a compositor write retains the previous transaction and deadline.

The normal confirmation deadline remains 30 seconds. An observed change without
a qualifying frame rolls back. If a compositor call may have changed the display
but its result cannot be observed, the existing guardian retains the ambiguous
intent for review; it does not replay the write or guess a rollback serial.
Later local changes are preserved. These distinctions also apply during shutdown
and recovery; automatic restoration is not promised for an ambiguous outcome.

## Desktop and encoder boundaries

The current implementation requires **GNOME Wayland** for its owned Mutter
virtual displays. The presence of the ScreenCast D-Bus name on an X11 session
does not establish that `RecordVirtual` can create a monitor. In Mutter 48.7,
`meta_monitor_manager_create_virtual_monitor()` returns an unsupported error
when the backend lacks the virtual-monitor method; the native backend supplies
that method and the XRandR backend does not. See the upstream
[manager](https://github.com/GNOME/mutter/blob/48.7/src/backends/meta-monitor-manager.c),
[native backend](https://github.com/GNOME/mutter/blob/48.7/src/backends/native/meta-monitor-manager-native.c)
and [XRandR backend](https://github.com/GNOME/mutter/blob/48.7/src/backends/x11/meta-monitor-manager-xrandr.c).

Keeping X11 is a separate implementation route: capture the X11 desktop, provide
a suitable resizable Xorg output, feed the existing host-memory video contract,
and implement matching input geometry and lifecycle recovery. Resizing a physical
HDMI output through RandR changes the local desktop and is limited by its modes.
A separate dummy Xorg desktop is possible but is a separate desktop, not a
transparent resize of the current session. Neither route is implemented here.
Wayland is a requirement of this implementation, not of the UU network protocol.

Portrait **dimensions** such as 720x1280 are covered by simulated mode selection.
Nonidentity monitor transforms (90/180/270 degrees), reflected and mirrored
layouts remain outside the current topology/input scope. Native geometry APIs
and normalized SendInput translation retain their existing implementation;
correct corner/center clicks after a resize still require a real UU session,
preferably one exclusive virtual output for the first acceptance run.

| Backend | Existing data path and remaining differences |
| --- | --- |
| NVIDIA | Vulkan DMA-BUF capture, GPU memory export and NVENC; fresh sessions are required for changed dimensions. |
| AMD | Existing VA-API backend: Vulkan readback to BGRA memfd, D3D11 upload/readback, NV12 conversion, hardware compression. It is not zero-copy; dynamic size changes inside one encoder session are rejected. |
| Intel | Not supported by the current AMD backend: `uurb_vaapi_device()` explicitly selects vendor `0x1002`. Intel VA-API/QSV and its device/capability handling have not been added or validated. |

See [AMD backend limits](amd-vaapi.md) for AQ, asynchronous encoding, HDR and
other differences. A successful H.264 qualification does not qualify HEVC.
Do not silently change codec for an already negotiated UU stream. Prefer a
separately negotiated H.264 stream for the first acceptance run; if that client
cannot select it, codec advertisement/fallback needs additional work.

## Wine installation

Both `uuway setup` and the source `install.sh` use the same source preflight.
Automatic source selection is exact: Ubuntu 24.04 uses noble/ubuntu; Debian 13
uses trixie/debian. Unknown releases, inconsistent codenames and mismatched
existing `winehq*` source files stop before new privileged Wine actions. The
source installer runs this check before installing build dependencies when Wine
11.0 is missing. Existing matching source files are preserved. Every installed
Wine package is constrained to 11.0.*, with the existing apt preference file.
Other distributions/releases may supply a reviewed Wine 11.0 installation
separately. `ID_LIKE` is not used to mix binary repositories.

These checks do not make an Ubuntu-built binary bundle ABI-compatible with
Debian. Build and qualify the native AMD runtime against the target distribution's
FFmpeg/VA-API/Wine dependencies before deployment. The noble references in the
fixed Ubuntu build container are intentional and are not host install settings.

## Reproducible checks

The focused suite uses fake Mutter replies and synthetic ACKs; the existing
broker tests also use isolated fixture producer processes. No live desktop is
changed and no real UU controller is involved:

```sh
python3 -m unittest \
  tests.test_source_installer tests.test_debian_wine_source \
  tests.test_native_adaptive_resolution tests.test_native_display_config \
  tests.test_native_display_topology tests.test_native_display_guardian \
  tests.test_native_virtual_display tests.test_native_display_confirmation \
  tests.test_uu_native_capture_broker tests.test_uuway_cli \
  tests.test_native_input_translate tests.test_amd_backend \
  tests.test_native_runtime_package tests.test_native_service -q
bash -n install.sh
```

For a separately authorized host-driver preflight, the explicit-device probe
encodes only generated test patterns, then decodes metadata for every frame.
It returns nonzero on any codec error or geometry mismatch:

```sh
python3 tests/probes/vaapi_geometry.py --device /dev/dri/RENDER_NODE --codec h264
python3 tests/probes/vaapi_geometry.py --device /dev/dri/RENDER_NODE --codec all
```

This FFmpeg check does not execute UUWay's Winelib/NVENC ABI or prove the full
stream. Full native/Wine build, native hardware tests, GTK/UI tests and actual UU
acceptance are separate checks. Tests requiring unavailable Wine/MinGW/Xvfb
must be reported as skipped rather than passed.

## Minimum live acceptance, after explicit deployment approval

1. Review the exact dependency/source/pin, input-rule, user-service and bundle
   installation changes. Do not run an unreviewed one-step installer on a live
   desktop. Preserve existing remote access and obtain a separate recovery SSH.
2. If switching X11 to Wayland, agree a logout window and save local applications
   first. GDM logout ends that local desktop; independent xrdp sessions must be
   assessed separately because a user's systemd services and D-Bus can be shared.
   Keep xrdp configuration and services unchanged.
3. Install a distribution-matched build, Wine 11.0 and the official UU host in an
   owned prefix. Grant capture/input explicitly. No autologin, driver replacement,
   firewall change, xrdp restart or machine reboot is required by this plan.
4. Connect with the actual UU controller. Record only bounded request/mode/frame
   metadata locally. First establish whether window resizing and phone rotation
   emit IDD or Win32 size requests; if they do not, stop claiming automatic
   viewport adaptation and document that protocol boundary.
5. On one owned virtual output with a separately negotiated H.264 stream, test
   landscape/portrait sizes, rapid requests, A-B-A, disconnect/reconnect and a
   missing-frame rollback. Check decoded dimensions and click all four corners
   and the center after every accepted size. Exercise the real encoder lifecycle;
   a server-side manual resize alone does not satisfy this acceptance.
6. Exit Super Screen and verify restoration of the saved physical layout. For
   rollback, stop only owned UUWay services, restore their saved user configuration,
   and select GNOME on Xorg at the next agreed login if needed. Confirm physical
   output restoration over the preserved recovery access. Never restart the
   existing SSH or xrdp services as part of this rollback.
