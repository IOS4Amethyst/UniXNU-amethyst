# UI-working boot checkpoint

## Hardware evidence, 2026-11-02

Redmi Note 14 Pro+ 5G, amethyst/SM7635, iPadOS 17.7.11 (21H461).
User observed a white Apple UI with “Press home to upgrade”. Scale2 made text
larger. Touch/Home input absent. This is UI presentation, not a working desktop,
completed upgrade, hardware watchdog or GPU qualification.

| Candidate | Image SHA256 | Observed result |
| --- | --- | --- |
| amethyst-cpu-present | af4d99934bb18ff22de2a1ea1153f3f986f9bba9d125d8dd1da515abe96c5e05 | First visible UI, 149 rounds, six completed swaps |
| amethyst-cpu-present-scale2 | 6913b06fa1fddb1948d8922e56b15cfa0401df4c4aa759fc1c1691d9d0cb8e3c | Larger UI, 144 rounds, twelve completed swaps |

Both use kernel UUID E37ABB7B-1B56-31D9-9351-90EDCD61DEDB and prelinked kernel
SHA256 ffd9eb12cc7c07de5a1ca53980685a543944ed1d53d1d5631977a664e65f48df.
Scale2 logger uptime294s, errors0, GPT1/1. First ten swap completion delays
1743–2525ms, last two98ms: these are end-to-end delays, not memcpy-only timings.
The ring wrapped, so absence of service exits in its tail is not an all-boot claim.

## Effective display choices

```
offline-mobile-graphics-load=1 offline-legacy-register=1
offline-legacy-phase=4 offline-legacy-gop=1 offline-legacy-pm=1
offline-legacy-unit-scale=2 offline-legacy-validate=1
offline-legacy-transfer=1 offline-legacy-cpu-present=1 sheng-log=1
```

These extend the existing authenticated root/userland baseline, not a universal
standalone boot command. Keep its other arguments and original trust bundle.
Exactly one numeric setting for each argument is required (last value wins).
GPU offer/vsync are not enabled by these settings. The unknown timing query
slot345 remains guarded; last-client cleanup may still panic.

## Sources now preserved in the repository

- `BootLog.c`: full-block checkpoint header source; stack diagnostic; no UFS
  writes from the panic hook. Normal periodic UFS writes remain enabled.
- `AmethystDisplayProbe.cpp`: generator input with boot-long display/nub
  diagnostic references, inactive-read counter and bounded raw state reads.
  Do not regenerate it from the unmodified upstream probe.
- `BootScanoutOwnership.c`: release the native video console at normal-thread
  scanout handoff before ownership publication; early stage hook remains inert.
- `drivers/NativeMobileDisplay`: reject pipeline-disabled swaps and avoid
  evicting accepted requests from the tracking table.
- `pack-log.py`: explicit, duplicate-checked display settings and RAM-seed
  replacement. `kernel-uuid.py` extracts LC_UUID rather than assuming an offset.

The probe retains one reference per published object until reboot (one probe
per boot). Repeatable teardown and retained internal subobjects are not claimed.

## Reproduction boundary and packaging

The successful builds used recorded XNU recipes plus the exact recovered Legacy
ABI/binding audit, isolated linking and mandatory prelink. This repository still
does not contain a clean-checkout end-to-end reproduction of that scratch build.
Do not call ordinary `unixnu build kernel` byte-identical to this candidate.
Do not hand-assemble pe_init.o flags: these candidates use the unchanged original
object SHA25679f82aa98c6e7ad2f2e31982475831fc6c4d5189c7cca8cf2c3fa4b92299e11c.
See OBJECT-BUILD.md and the detailed audit for the earlier flag regression.

With that qualified prelinked kernel and the verified corrected RAM seed:

```sh
python3 scripts/log/pack-log.py --kernel /path/to/kernel-cpu-present-prelinked.macho \
  --out /path/to/staging/amethyst-cpu-present-scale2 --display-probe --display-pm \
  --display-unit-scale 2 --display-validate --display-cpu-present \
  --data-seed /path/to/watchdog-disabled.packed
```

The packer currently expects the local owner-union baseline and firmware shim
source. This is packaging replay with private/local inputs, not a portable build.
Preserve old images and photos. Compile → link → prelink → pack; no flashing.

## Temporary watchdog policy

Original watchdogd trapped on failure to discover its backend. A copied RAM Data
seed disables only com.apple.watchdogd/com.apple.watchdog, with root-owned0644
disabled.plist and disabled.migrated. Volume UUID preserved. Actions run
41260884740 passed host checks; user confirmed watchdogd no longer launches.
Seed SHA25640d76e6f2a856f92ca999f07a38e7c0a89097e04223dde3ae74e0bb1c047545e.
Remove this override when a real backend is available. Never claim watchdog
protection. No signed executable/root image edited, no Android userdata writes.

Binary images, Apple seed contents, private signing material, and Android logs
are not included in this source commit. Local photos remain beside their images.
