# Active donor migration — iPhone17,2 / D94AP

Use `ios-27.0.1-24A446-d94ap`, checked-in donor profile and reviewed fixture
additions. Root OS UUID `E3F4AED0-4454-4D5B-833C-E27E99C4F67B`, SystemOS UUID
`8B599D21-9ABD-4050-8BA2-C61349890BED`; kernel SHA
`fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95`.
Verified root policies: run43184772128. New D94 Data seed required; old Data UUID
must not be reused without an explicit content/provenance audit. Five private
imports remain unsupported; matching success is not runtime qualification.
Everything below that references iPhone15,4 is historical bring-up evidence.

---

# iOS 27 (`ios27`) bring-up state

Branch `ios27` moves the port from iPadOS 17.7.11 to iOS 27.0.1 (`24A446`) with
the iPhone15,4 identity. This file records where that branch stands, the numbers
that justify the configuration, and what is still unproven. Nothing here is a
hardware result: no iOS 27 image has been booted.

The iPadOS 17 port stays the working reference (`ROOT-BRINGUP.md`,
`scripts/log/WORKING-BOOT.md`).

## Current state

| Repository | Branch | Head |
| --- | --- | --- |
| `IOS4Amethyst/UniXNU` (core) | `ios27-release-config-level` | `99fa60b` |
| this repository | `ios27` | this commit |

Taken over from the earlier agent on 2026-11-07, after the iPadOS 17 port reached V60 (touch, GPU, brightness).

The kernel links and prelinks at RELEASE for the iPhone15,4 `24A446`
kernelcache. The emitted collection is an experimental allow-missing image: its
report says `mapping_complete`, `runtime_qualified` and `loadable` are `false`.
It has not been put in a payload, a boot image or on the phone.

Builds that passed:

| Run | Device | Core | `kernel.macho` sha256 |
| --- | --- | --- | --- |
| 42274081825 | `383c392` | `01ec839` (with fileset reference) | `2e3c26e5…` |
| 42277592719 | `383c392` | `bc55b3f` | `bc82edaf…` |
| 42282422339 | `1cf8d07` | `bc55b3f` | `56a8dcf9…` |
| 42282725885 | `1d00cef` | `bc55b3f` | `c48492f8b0e24e089bf8a8b9aab960a3eb9743a32494b830ed1b715c3b00a4d7` |

All four used kernelcache `b508b34e17ded04ea54c51399bc486592fb75b8d4629b9bf0c30fbc36d0071b2`.

## Why RELEASE

The iOS 27 fileset prelink keeps 256 kexts with `__DATA_CONST` in
`0xfffffff007c18000..0xfffffff008198000` and gives `com.apple.kernel` the
`0xd8000` slot. The native kernel's `__DATA_CONST` measures:

| Level | `__DATA_CONST` | Fits `0xd8000` |
| --- | --- | --- |
| RELEASE | `0xb8000` | yes, `0x20000` spare |
| DEVELOPMENT | `0x120000` | no, over by `0x48000` |

DEVELOPMENT adds `mach_assert`, `config_xnupost` and `development` to
`KERNEL_BASE`. Every board file gated on DEVELOPMENT or DEBUG sits behind a
config macro this board leaves off, so nothing board-specific is lost. The
bring-up audit behind that claim: DEBUG/DEVELOPMENT tripwires are nested under
unset `XNU_OWNER_*`; `-enable_kprintf_spam` is silently ignored at RELEASE;
`debug=0x14e` survives but is inert without a DT `/chosen` `debug-enabled`;
`-textconsole` survives; `panic_screen = false`; `BootLog.c` is not built.

The level is a parameter of `build-xnu-4k-baseline.sh` defaulting to
DEVELOPMENT, so every other configuration stays byte-identical, and the change
lives on a branch, not on any main.

## What the prelink leaves open

The last four builds produce one pairing (identical `missing`, `stubbed` and
segment layout), so the result is reproducible apart from build timestamps:
950 byte groups differ between native images and all of them are LC_UUID, the
`__DATE__`/`__TIME__` stamp and version strings.

Matched: 8632 of 20907 kernel functions, 1312 data objects, 1657 of 2954
kext-facing targets named, conflicts `double-bind: 143, vtable-correction: 1`.

Open, bounded by `scripts/policies/ios-27.0.1-24A446/prelink-regression.json`:

- **321 unresolved cluster references over 98 addresses.** All are data in
  retained kext data sections: 37 metaClass metaobject slots, 19 vtable slots,
  36 kernel `__DATA` objects addressed from kernel text, 92 global keys and
  `__DATA` symbols. Most referenced: `__ZN8OSNumber9metaClassE+0x18` (14 sites),
  `_zone_ro_size_params+0x9a8` (12), `_gIOPowerPlane+0x40` (11).
- **1530 unmatched kernel functions**, bound to `_unixnu_fileset_unmapped`, which
  panics with the call site. Every site is a data operand inside a function body
  (literal pool), not a function start.

A stop in `_unixnu_fileset_unmapped` is therefore the expected first hardware
result: it names the kext and the original address. It is a measurement, not a
regression. Core `scripts/report-prelink-unnamed.py` and
`scripts/summarize-prelink-report.py` print both sets.

## Payload and first image (2026-11-07)

- The `UNIXNU_UNIQUE_CHIP_ID` secret holds the platform seed of the iPadOS 17 images (read from their ADT), so both OS
  profiles present the same device.
- `ios27-kernel.yml` now runs in the `ARTIFACT_PASSPHRASE` environment and uploads only `unixnu-ios27-build-encrypted`
  (aes-256-cbc, pbkdf2 200000): this repository is public and the outputs hold the prelinked Apple kernelcache. The payload
  step gained `--os`, a bash 3.2-safe empty Data-seed array, and the volume grew to 128 MiB (the files take 76 MiB).
- Run 42295993833 built the payload: `kernel.macho`, `.adt`, `.vroot` (root policies), no trust cache, no Data seed.
- `scripts/pack-ios27.py` packs it RAM-only: header, boot shim and FAT geometry of the iPadOS V60 image, the GMU_Pool
  firmware (`.scratch/uefi-gmu/amethyst-gmu-pool.fd`), the FAT's `EFI/UniXNU` replaced by the payload's. DarwinBoot is
  pinned at 3c75734 for both profiles. Image `staging/amethyst-ios27-i1` (SHA-256 350fb401...), not booted yet.
- Expected first result: a stop in `_unixnu_fileset_unmapped` (or earlier) on the text console. RELEASE builds no
  `BootLog.c`, so there is no UFS log: the screen is the record.

## On the phone (2026-11-07)

- i1 (old firmware DarwinBoot): "returned unsupported". That DarwinBoot predates UniXNU/DarwinBoot 3c75734 and refuses
  arm64e kernels and MH_FILESET collections. A second DarwinBoot cannot be loaded beside it (DarwinBootEntry returns
  EFI_ALREADY_STARTED when the loader protocol exists), so it is replaced in place.
- The CI firmware build cannot run: uefi-amethyst needs the proprietary `Binaries/` tree. DarwinBoot 3c75734 was built
  alone instead, against mu_basecore cfb93724 and core's UniXNUPkg:
  `build -p DarwinBootPkg/DarwinBoot.dsc -m DarwinBootPkg/Driver/DarwinBoot.inf -a AARCH64 -t CLANGPDB -b RELEASE`
  (`CLANGPDB_BIN=/usr/bin/`, an empty `MdePkg/Library/MipiSysTLib/mipisyst/library/include` stub; Brotli need not build).
  `scripts/patch-darwinboot.py` swaps it into the GMU_Pool firmware (`.scratch/uefi-darwinboot/amethyst-gmu-pool-db3c75734.fd`).
- i2 (new DarwinBoot): green handoff bar, then nothing; no kernel text.
- i3 (i2 plus core `patch-xnu-entry-probe.py --reset-delay 6` on the kernel): blue = kernel entry ran, magenta = reached
  MMU enable, reboot ~6 s later = MMU on. Core 7505af5 fixes the probe's symbol parsing for filesets, so CI can apply it.

## Not proven yet

- No userdata build for an iOS 27 image has been made.
- The kernel has not shown any output on the phone yet (see above).
- No trust cache is in the payload, so userland cannot start even if the kernel does.
- `Data` seed and root-policy bundle for `24A446` are inputs that exist as
  workflow artifacts, not as tested images.

## Next steps

1. Build the payload: the `iOS 27 kernel` workflow's `build_payload` input with
   `data_ramdisk` (the Data seed workflow's `owner-data.cdr`) and, for a root
   read, `root_policies` (the root-policy workflow's bundle). The
   `UNIXNU_UNIQUE_CHIP_ID` secret must exist.
2. Pack a RAM-only image with `./unixnu bootimg` and start it with
   `fastboot boot`; never flash, erase, `set_active` or `oem`, and keep
   `sheng-data-persist=0`.
3. Read the first stub panic and decide which of the data gaps to close first.
   Extend the fileset matcher (metaobject/vtable identity); never hardcode an
   original address. The regression policy above shows every improvement.
