# UniXNU for Xiaomi Redmi Note 14 Pro+ 5G (`amethyst`)

Device port of [UniXNU](https://github.com/UniXNU/UniXNU) for the Xiaomi Redmi Note 14 Pro+ 5G.

> **Branch `ios27`:** experimental iOS 27.0.1 (`24A446`) port with an iPhone 15 identity (arm64e). The RELEASE kernel links and prelinks; see [iOS 27](#ios-27-ios27).

| Property | Value |
|---|---|
| Device | Xiaomi Redmi Note 14 Pro+ 5G |
| Codename | `amethyst` |
| SoC | `SM7635` (Adreno 810 GPU) |
| Architecture | ARM64 |
| Main OS target | iPadOS 17.7.11 (`21H461`) |
| Device repository | [IOS4Amethyst/UniXNU-amethyst](https://github.com/IOS4Amethyst/UniXNU-amethyst) |
| UEFI repository | [IOS4Amethyst/uefi-amethyst](https://github.com/IOS4Amethyst/uefi-amethyst/tree/amethyst) |
| Status | **iPadOS 17 boots from RAM to a usable home screen with GPU-accelerated UI and touch** |

## Current status (2026-11-07)

Latest hardware-tested image: **V52** (RAM-only `fastboot boot`).

- **Boot:** UEFI → UniXNU → DarwinBoot → XNU → launchd → SpringBoard. Setup is
  skipped and the lock screen is dismissed (AppleKeyStore stand-in that reports
  "no passcode, unlocked").
- **GPU:** the UI is composited on the Adreno GPU. A native Adreno kernel driver
  and a Metal plugin translate Metal to Vulkan and run it on Mesa Turnip.
  backboardd, SpringBoard and apps render on the GPU (blur works).
- **Display:** the GPU copies each frame to the panel at 120 Hz vsync. Swaps
  finish in about 10 ms, so the UI is smooth. Text and icons render correctly
  since V52.
- **Touch:** Goodix touch controller, delivered to iOS as a HID digitizer.
  Hardware buttons work.
- **CPU:** all cores are online (SMP) and a CPU frequency governor is running.

### Known issues

- **Bottom-left/right corner touches:** iPadOS stops responding to touch after a
  touch in a bottom corner. The kernel keeps delivering touches; the hang is on
  the iOS side, probably the iPadOS corner swipe (Screenshot / Quick Note).
  A kernel corner guard (`amethyst-touch-corner=N`) is not enough on its own.
- **GPU recovery events:** the log still shows occasional GPU stalls recovered
  by a kick. They are not visible in the UI in V52.
- **Some GPU clients have no device:** mediaserverd, widget and poster renderers
  report `Vulkan -3` and fall back.
- **No persistence:** the Data volume is a RAM seed packed into the boot image,
  so nothing survives a reboot. watchdogd is disabled in that seed because there
  is no watchdog backend.
- **Not implemented:** audio, Wi-Fi, Bluetooth, cellular, cameras, sensors,
  battery/charging reporting, suspend.

### Hardware support

| Area | Status | Notes |
|---|---|---|
| UEFI boot (`fastboot boot`) | ✅ Working | RAM only, nothing is flashed |
| XNU kernel, SMP, CPU frequency | ✅ Working | All clusters online, governor running |
| Root and Data | ✅ Working | Root read from the `unixnu` partition; Data is a volatile RAM seed |
| Display | ✅ Working | GPU hand-off presenter, 120 Hz vsync |
| GPU (Adreno 810) | ✅ Working | Native Adreno driver, Metal → Vulkan (Turnip) |
| Touch | 🟡 Mostly working | Bottom-corner touches stop iOS input |
| Buttons | ✅ Working | |
| Lock screen / Setup | ✅ Skipped | Data seed + AppleKeyStore stand-in |
| Persistence | ⏳ Not started | Needs a validated write plan for the `unixnu` partition |
| Audio, Wi-Fi, Bluetooth, cellular, cameras, sensors | ⏳ Not started | |

**Status legend:** ✅ verified on the device · 🟡 works with known problems ·
⏳ no runtime evidence yet.

### Milestones

| Date | Image | Result |
|---|---|---|
| 2026-11-02 | `amethyst-cpu-present-scale2` | First Apple UI on screen (CPU presentation) — the `main` checkpoint |
| 2026-11-04 | Skip Setup v3 | Setup skipped, lock screen reached |
| 2026-11-04 → 05 | V18–V44 | Touch to iOS, SMP, CPU governor, Adreno power-up, command processor, first GPU draw |
| 2026-11-05 | V45–V47 | Metal accelerator published, GPU add-on installed, lock screen skipped |
| 2026-11-06 | V48–V51 | backboardd on the GPU, uncompressed framebuffers, GPU frame hand-off (smooth) |
| 2026-11-06 | V52 | Add-on rebuilt from the current asteroids stack: garbled UI and freezes fixed |

The per-image record (hashes, boot arguments, log evidence) is in
[`scripts/log/WORKING-BOOT.md`](scripts/log/WORKING-BOOT.md): up to the
2026-11-02 checkpoint on `main`, up to V44 on `amethyst-touch-gpu-bringup`.
V45–V52 are recorded locally and not pushed yet. Earlier history is
in [ROOT-BRINGUP.md](ROOT-BRINGUP.md), [UFS-AUDIT.md](UFS-AUDIT.md) and
[`scripts/log/ABORT-AUDIT.md`](scripts/log/ABORT-AUDIT.md).

## Branches

| Branch | Purpose |
|---|---|
| `main` | Hardware-confirmed Apple UI checkpoint (2026-11-02). Base of every other branch. |
| `amethyst-touch-gpu-bringup` | Touch, SMP, CPU governor and GPU bring-up sources (V18–V44). |
| `skip-setup-seed` | CI job that builds the Data seed: skips Setup, disables crash-looping daemons, turns off compressed/deep framebuffers. |
| `gpu-addon` | CI job that builds the GPU add-on (Metal plugin + Turnip) and owner trust bundle. Artifacts are encrypted. |
| `ios27` | Experimental iOS 27.0.1 (`24A446`) port with an iPhone 15 identity (arm64e). The RELEASE kernel links and prelinks; the payload and Data seed are next. |
| `ios27-iphone15-pins` | Root image pins for iPhone15,4 `24A446`, used by `ios27`. |

The V45–V52 kernel sources (AppleKeyStore stand-in, sandbox grant, corner guard,
swap diagnostics, `pack-log.py --aks/--fb-gpu`) are not on any branch yet.

### iOS 27 (`ios27`)

Nothing has been tested on hardware yet. The source-storage root path has been
ported, and the kernel now builds at the RELEASE configuration, which its
constants need: the iOS 27 prelink places `com.apple.kernel` in a `0xd8000`
`__DATA_CONST` slot, while a DEVELOPMENT kernel measures `0x120000` and a
RELEASE one `0xb8000`. The reference-enabled and the plain RELEASE link both
pass, and the prelink emits a kernel collection; the payload and the Data seed
are the next steps. Kernel CI runs on GitHub `macos-26` runners.

## Safety

- Every test image is started with `fastboot boot` and runs from RAM. Nothing is
  flashed, erased or made active.
- Android `userdata` is never written. On split devices, persistent Data and
  log/export opens are disabled because userdata is F2FS.
- The boot log is written only to the last 32 MiB of the dedicated `unixnu`
  partition and is pulled from LineageOS Recovery with
  [`scripts/log/pull-log.py`](scripts/log/pull-log.py).
- Board defaults keep `sheng-data-persist=0`, `sheng-log=0` and
  `payload.adt.persistent_data=false`; test images enable the log explicitly.
- Automatic UFS controller takeover is off (`native-ufs-root=0`).
  See [UFS-AUDIT.md](UFS-AUDIT.md).
- Proprietary firmware, IPSW-derived files and decrypted artifacts are kept out
  of git.

## Building

There are two paths.

### Hardware-tested images (`scripts/log`)

The images above are built by
[`scripts/log/pack-log.py`](scripts/log/pack-log.py) from a baseline image, a
prelinked kernel, the UEFI firmware, a Data seed and, for GPU images, the
add-on and owner trust bundle. These inputs come from local builds and CI
artifacts that contain proprietary or IPSW-derived files, so they are not in
the repository. `scripts/log/WORKING-BOOT.md` lists the exact inputs and options
for each image. A clean-checkout reproduction of these images is not available
yet.

### Base UniXNU build

These steps build the board kernel, payload, firmware and boot image with the
standard UniXNU tools.

#### Prerequisites

- Linux with the UniXNU open-source Mach-O toolchain, or macOS with Xcode.
- Git with recursive submodule support.
- Python 3.11+, mtools and dtc.

On Linux, install the toolchain once from the UniXNU checkout:

```bash
sudo scripts/linux/setup-toolchain.py -j "$(nproc)"
scripts/linux/setup-toolchain.py --check
```

The commands below assume both repositories are cloned side by side and run
from the UniXNU core repository.

#### 1. Clone the repositories

```bash
git clone --recurse-submodules --branch amethyst-embedded-payload \
  https://github.com/IOS4Amethyst/UniXNU.git

git clone --recurse-submodules \
  https://github.com/IOS4Amethyst/UniXNU-amethyst.git
```

#### 2. Prepare the bootstrap environment

```bash
cd UniXNU
DEVICE_DIR=../UniXNU-amethyst bash ../UniXNU-amethyst/scripts/ci-bootstrap-candidate.sh
```

#### 3. Configure and build the kernel

```bash
./unixnu defconfig amethyst_defconfig -D ../UniXNU-amethyst
./unixnu build kernel -D ../UniXNU-amethyst
```

#### 4. Clone the UEFI repository

```bash
git clone --recurse-submodules --branch amethyst \
  https://github.com/IOS4Amethyst/uefi-amethyst.git ../uefi-amethyst
```

#### 5. Build the payload and firmware

```bash
./unixnu build payload \
  --unique-chip-id 0x1234567890abcdef \
  --uefi ../uefi-amethyst \
  --edk2 ../uefi-amethyst/Mu_Basecore \
  -D ../UniXNU-amethyst

./unixnu build firmware --uefi ../uefi-amethyst -D ../UniXNU-amethyst
```

Replace the example chip identifier with your own value when required.

#### 6. Build the boot image

```bash
curl -fL \
  "https://raw.githubusercontent.com/IOS4Amethyst/uefi-amethyst/refs/heads/amethyst/Resources/DTBs/amethyst.dtb" \
  -o ../UniXNU-amethyst/live.dtb

./unixnu bootimg \
  --uefi ../uefi-amethyst \
  --dtb ../UniXNU-amethyst/live.dtb \
  -D ../UniXNU-amethyst
```

Amethyst's Android header-v4 bootloader exposes the installed vendor ramdisk
instead of the generic ramdisk given to `fastboot boot`. Until embedded payload
support is upstream, use UniXNU branch
`IOS4Amethyst/UniXNU:amethyst-embedded-payload` at or after `20437c1` with UEFI
branch `IOS4Amethyst/uefi-amethyst:amethyst` at or after `4e6782ba`.

#### Optional: userdata image

Needs about 16 GB of extra disk space.

```bash
./unixnu build userdata -D ../UniXNU-amethyst
```

## Reporting progress

Include the pulled boot log (`pull-log.py` output) or the build output, and say
where execution stops. A successful build is progress, but behaviour on the
device has to be verified separately.
