# UniXNU for Xiaomi Redmi Note 14 Pro+ 5G (`amethyst`)

Device port of [UniXNU](https://github.com/UniXNU/UniXNU) for the Xiaomi Redmi Note 14 Pro+ 5G.

| Property | Value |
|---|---|
| Device | Xiaomi Redmi Note 14 Pro+ 5G |
| Codename | `amethyst` |
| SoC | `SM7635` |
| Architecture | ARM64 |
| Device repository | [IOS4Amethyst/UniXNU-amethyst](https://github.com/IOS4Amethyst/UniXNU-amethyst) |
| UEFI repository | [IOS4Amethyst/uefi-amethyst](https://github.com/IOS4Amethyst/uefi-amethyst/tree/amethyst) |
| Status | **Apple UI displayed through CPU GOP presentation; touch and Home input not implemented** |

## Bring-up status

### Current hardware result (2026-11-02)

RAM-only `fastboot boot` reached a white Apple UI with “Press home to upgrade”.
Scale 2 is confirmed visibly larger. The scale-2 run completed 144 logger rounds
with zero write errors and 12 copied/completed swaps. This is not a usable
desktop or completed upgrade: touch/Home input are absent, initial presentation
is slow, GPU rendering is not validated, and watchdogd is temporarily disabled
in the volatile Data seed because no watchdog backend is provided.

Known UI-working image: `amethyst-cpu-present-scale2`, SHA256
`6913b06fa1fddb1948d8922e56b15cfa0401df4c4aa759fc1c1691d9d0cb8e3c`,
kernel UUID `E37ABB7B-1B56-31D9-9351-90EDCD61DEDB`.
Android userdata is preserved; experimental logs use the last 32 MiB of the
dedicated `unixnu` partition. Nothing is flashed by the boot command.

See [current build and hardware evidence](scripts/log/WORKING-BOOT.md),
[detailed investigation](scripts/log/ABORT-AUDIT.md) and
[root bring-up history](ROOT-BRINGUP.md). The historical notes below describe
earlier milestones and defaults; they are not the configuration of this image.

**Current safety override:** automatic UFS takeover is disabled
(`native-ufs-root=0`, no probe argument). Earlier root-enabled candidates stalled
on hardware and are not qualified. See [UFS audit](UFS-AUDIT.md) for source
evidence, remaining ownership/DMA requirements, and host-only test limits.

### Split-device storage safety

`unixnu` is preferred for root reads, with `userdata` fallback only when no
`unixnu` entry exists. On split devices, persistent Data and log/export opens
are disabled: Android userdata is F2FS, not a safe raw-write region. The
Partitioner's `reserve_mib` currently leaves an unallocated tail above unixnu;
it does **not** reserve blocks inside Android userdata. Do not run Apply based
on the earlier claim that this gap protects sheng persistence. A dedicated,
validated persistence allocation and reviewed device-write plan are still needed.
Host parser and store/log refusal tests pass; hardware writes are untested.
Board defaults explicitly set `sheng-data-persist=0`, `sheng-log=0`, and
`payload.adt.persistent_data=false`; the driver guard is defense in depth.

Host build evidence (2026-10-30): `./unixnu build kernel -D ../UniXNU-amethyst
-j 8` completed, including ShengUfs `71eeacb` and prelink. Output relative to
UniXNU: `out/amethyst/ipados-17.7.11-21H461/amethyst/kernel/kernel.macho`, SHA-256
`4cbec82154b2a9f229ea265246055a07dc4bd46e7cc42e8b5ff979215818c25a`.
The unchanged Static/System trust-cache modules fetched with `./unixnu ipsw
fetch` produced `out/ipsw/21H461/original-static-system.bundle`, SHA-256
`7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`, matching
the OS profile allow-list. Payload preflight stops on the missing platform seed
(`--unique-chip-id`); a compatible Data seed is also still absent. No new boot
image or hardware validation is claimed.

Follow-up payload assembly reused the existing kernel87 ADT platform seed
privately (not committed). Data is optional for this kernel-only test. The
board now omits PMIC/RTC ADT export markers: their SPMI-local registers cannot
be flattened into MMIO, and no corresponding driver is selected. The controller
still exports its real MMIO resources; hardware descriptions are retained.
`scripts/check-adt-spmi.py ../UniXNU <compiled-hardware.dtb>` checks this policy.
Payload assembly passes with the device UEFI path and original trust bundle:
`out/amethyst/ipados-17.7.11-21H461/amethyst/payload/payload.img` (80 MiB), SHA-256
`824a06ce29f514befefd9331d8f21287e410bb46713325373048736ac116e25e`.
It has no Data seed and is not a userland-ready image. Boot-image packaging and
hardware testing remain pending; preserve the kernel87 firmware baseline.

UFS argument audit: the prior candidate omitted the upstream-required
`native-ufs-root=1`; `native-ufs-allow-unverified=1` does not enable the driver.
Board defaults now explicitly request root mode; `scripts/check-adt-spmi.py`
also checks root mode, CONFIG_XNU_NATIVE_UFS and persistence-off policy.
The corrected payload SHA-256 is
`d4842795b33c79aa97b55989166c7ed5ee3cca5d14b66289e6e0f559ff8b0cec`.
A separate host-packed kernel-only test preserves the exact kernel87 shim,
firmware FD and firmware DTB; image SHA-256
`f32ddd6e74a3c490b9f4abb4b84319277d14391806f5224b3c8a394c494cc05b`.
Its packed configuration and decompressed payload were verified. This does not
establish controller ownership, DMA/command correctness, root UUID validity,
root mounting, or userland on Amethyst. Those remain hardware/input milestones,
not satisfied by build success. No partition writes or hardware boot performed.

Hardware follow-up: both root-enabled and CPU-cluster-fix candidates remained
at green/blue/magenta bars. The cluster exporter defect was real but did not
resolve the reported stall. Offline disassembly found kernel87's entry and
SCTLR instructions unpatched, whereas `panic_screen=true` inserts entry/MMU
detours even with `--no-reset`. Board payloads now disable that instrumentation
and retain the native text-console patches. The extracted FAT kernel matches
the prelinked kernel byte-for-byte. The resulting native-entry candidate is
SHA-256 `707b4f59ec4a4e12bb56d4dcb57be3ecb931937bb1261c139e27bd27e2c5b2ce`.
This removes a verified baseline difference; causation and successful hardware
boot remain unproven. No claim that the stall is definitively fixed.

Source-console follow-up: `patches/xnu/early-text-console.patch` makes
`PE_create_console()` honor `-textconsole` before the later `vcattach()` path.
`scripts/check-source-console.py <patched-XNU-source> <owned-output-dir>`
executes that production function with host stubs for all display/argument
combinations. Kernel build/prelink succeeded after restoring omitted Apple
headers before archive compilation. Kernel SHA-256:
`b16455f0281e5e1bac762756a0078419f517e7fd4e4e52ac61519ee635aa2763`.
Host-packed source-console candidate SHA-256:
`edff0d4a0fa1d61938458c29da0d5b108ee43de5e853b920d2da590b3ace3562`.
The FAT kernel matches the prelinked kernel exactly (no binary entry probes).
This requires execution to reach console creation; hardware console recovery
and the underlying green-screen stall remain unverified.

Offline pre-console audit found `/arm-io/ranges` parent base zero in the new
exporter versus `0x01000000` in kernel87. XNU's `pe_arm_get_soc_base_phys()`
returns that field, and `pe_identify_machine()` returns immediately if it is
zero, skipping clock-frequency initialization. `payload.adt.arm_io_base` now
restores the nonzero kernel87 base; exported MMIO regs are correspondingly
relative, preserving their physical addresses. UniXNU's
`scripts/check-adt-io-base.py <hardware.dtb>` verifies every translated range
and rejects underflow. Payload rebuild and packed-image round-trip passed;
candidate SHA-256:
`fe7d3bc13d585f8931f0d2f4376f5354b8a9b905ad730b519ab6dbc582f867bb`.
This fixes a confirmed early platform-initialization mismatch, not proof of a
complete explanation for the hardware stall. Hardware validation is pending.

The port boots from RAM with `fastboot boot`. UEFI mounts the embedded payload,
UniXNU parses its configuration, DarwinBoot validates the kernel and ADT, and
XNU starts its scheduler, enters IOKit and reaches root mounting. The physical
GOP text console is visible on hardware. A guarded ApplePMGR provider satisfies
AMFI without advertising unimplemented power operations. The current panic is
`Failed to mount root device`; a compatible root image is not part of the
RAM-only test.

| Stage | Component | Status | Objective |
|---|---|---|---|
| 1 | UEFI | ✅ Working | Boots through RAM-only `fastboot boot` with stock Amethyst ClockDxe |
| 2 | UniXNU bootstrap | ✅ Working | Menu and input work on hardware |
| 3 | Payload | ✅ Working | Embedded FAT payload mounts and `unixnu.toml` is parsed |
| 4 | DarwinBoot | ✅ Working | Inputs validate and `ExitBootServices` completes |
| 5 | XNU kernel | ✅ Working | VM, scheduler, first context switch, IOKit, text console and AMFI/PMGR path pass |
| 6 | Storage and root filesystem | 🟡 Mount boundary reached | XNU attempts root mounting; provide and validate the configured userdata root |
| 7 | Userland | ⏳ Unverified | Verify launchd startup and a visible SpringBoard state |

**Status legend**

- ✅ Working — verified on the target device.
- 🟡 Build/preparation complete — artifacts or infrastructure exist, but runtime behavior is not confirmed.
- ⏳ Unverified — no successful runtime evidence yet.

> Successful compilation does not establish that the firmware can execute on the device or that the kernel can boot. Stage completion requires runtime evidence, preferably serial logs.

## Build instructions

### Prerequisites

- Linux with the UniXNU open-source Mach-O toolchain, or macOS with Xcode.
- Git with recursive submodule support.
- Python 3.11+, mtools and dtc.

On Linux, install the toolchain once from the UniXNU checkout:

```bash
sudo scripts/linux/setup-toolchain.py -j "$(nproc)"
scripts/linux/setup-toolchain.py --check
```

The commands below assume that both repositories are cloned into sibling directories and that all build commands are executed from the UniXNU core repository.

### 1. Clone the repositories

```bash
git clone --recurse-submodules --branch amethyst-embedded-payload \
  https://github.com/IOS4Amethyst/UniXNU.git

git clone --recurse-submodules \
  https://github.com/IOS4Amethyst/UniXNU-amethyst.git
```

### 2. Enter the UniXNU core repository

```bash
cd UniXNU
```

### 3. Prepare the bootstrap environment

```bash
chmod +x "../UniXNU-amethyst/scripts/ci-bootstrap-candidate.sh"

DEVICE_DIR=../UniXNU-amethyst bash "../UniXNU-amethyst/scripts/ci-bootstrap-candidate.sh"
```

### 4. Configure the device

```bash
./unixnu defconfig amethyst_defconfig -D ../UniXNU-amethyst
```

### 5. Build the XNU kernel

```bash
./unixnu build kernel -D ../UniXNU-amethyst
```

### 6. Clone the UEFI repository

```bash
git clone --recurse-submodules \
  --branch amethyst \
  https://github.com/IOS4Amethyst/uefi-amethyst.git \
  ../uefi-amethyst
```

If the repository already exists, update it instead:

```bash
git -C ../uefi-amethyst pull --recurse-submodules
git -C ../uefi-amethyst submodule update --init --recursive
```

### 7. Build the payload

```bash
./unixnu build payload \
  --unique-chip-id 0x1234567890abcdef \
  --uefi ../uefi-amethyst \
  --edk2 ../uefi-amethyst/Mu_Basecore \
  -D ../UniXNU-amethyst
```

Replace the example chip identifier with the appropriate value for your setup when required.

### 8. Build the firmware

```bash
./unixnu build firmware \
  --uefi ../uefi-amethyst \
  -D ../UniXNU-amethyst
```

### 9. Download the device tree blob (DTB)

```bash
curl -fL \
  "https://raw.githubusercontent.com/IOS4Amethyst/uefi-amethyst/refs/heads/amethyst/Resources/DTBs/amethyst.dtb" \
  -o "../UniXNU-amethyst/live.dtb"
```

### 10. Build the boot image

```bash
./unixnu bootimg \
  --uefi ../uefi-amethyst \
  --dtb ../UniXNU-amethyst/live.dtb \
  -D ../UniXNU-amethyst
```

The boot-image stage requires a valid DTB and a compatible UEFI implementation.

Amethyst's Android header-v4 bootloader exposes the installed vendor ramdisk
instead of the generic ramdisk supplied to `fastboot boot`. Until the embedded
payload support is upstream, use UniXNU branch
`IOS4Amethyst/UniXNU:amethyst-embedded-payload` at or after `20437c1` together
with UEFI branch `IOS4Amethyst/uefi-amethyst:amethyst` at or after `4e6782ba`.

## Optional build targets

### Build userdata

The userdata image may require approximately 16 GB of additional disk space.

```bash
./unixnu build userdata -D ../UniXNU-amethyst
```

## Current blockers

- **Root filesystem:** the RAM-only image reaches `Failed to mount root device`.
  Build and validate the configured userdata root before testing launchd.
- **Power management:** the ApplePMGR bridge only publishes the provider AMFI
  requires. Unimplemented power operations deliberately panic rather than touch
  unqualified registers.
- **Hardware support:** full display presentation, input, suspend, GPU and other
  board services remain unverified.

## Next milestone

Provide the root filesystem, verify UFS publication and mount the configured OS
volume. Keep root/userdata work separate from RAM-only kernel bring-up.

## Contributing and debugging

When reporting bring-up progress, include the relevant build output or serial log and identify the stage at which execution stops.

A successful compilation is useful progress, but hardware initialization and runtime behavior must be verified independently.
