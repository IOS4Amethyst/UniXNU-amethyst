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

## Skip Setup candidate (2026-11-04)

Goal: SpringBoard instead of "Press home to upgrade". Seed from CI run
41694126335 (branch `skip-setup-seed`, `scripts/log/skip-setup-seed.py`):
original seed -> watchdog override -> UniXNU seed-data-skip-setup.py values
with --no-auto-dim, files owned by mobile 501:501. The re-made watchdog seed
hashes 84a10047… (not the local 40d76e6f…; repacking is not byte-stable).
Seed `.scratch/ufs-audit/skip-setup-seed/skip-setup.packed` SHA256
`66c20db7fa955d248a22bd8335808d298f3d58bd69edf3415ebeb9a298df9c9f`.
Kernel: hardware-tested buttons-native prelinked kernel (power key works).
Flags: display probe/pm/scale2/validate/cpu-present, --buttons.
Image `staging/amethyst-skip-setup/Mu-amethyst-skip-setup.img`, SHA256
`a870567a99ef968916610588215696b2695b8dcd8adef6c0275847b5b00157ca`.
Pack checks pass. Hardware untested. Touch is still absent.

### Skip Setup run 1 and v2

Run 1 (`.scratch/log-results/result-skip-setup-1`, 839s, errors0): PosterBoard
launched (Setup skipped) but only 4 swaps at 137-146s, none afterwards; Apple
logo stayed. No SpringBoard corpses in the retained msgbuf, which starts at
210s because callservicesd/biomed/nsurlsessiond/bluetoothd respawn every 10s.
V2 seed (CI run 41702221224, macos-15-intel after two arm64 runner
starvations) also disables identityservicesd, locationd, CommCenter,
iconservicesagent (UniXNU skip-setup.md list for 21H461), bluetoothd and
callservicesd via launchd disabled.plist; watchdog overrides retained. Seed
SHA256 `3387fcea5ced55f04b54d5966b0e1c71436555908b2c13346d62968fe17539ce`.
Same kernel/flags as run 1. Hardware untested. Control image
`staging/amethyst-ci-watchdog-control` (CI watchdog seed, no Setup skip) also untested.

### Skip Setup run 2 (v2) and v3

Run 2 (`.scratch/log-results/result-skip-setup-2`, 754s, errors0): Apple logo
stayed. The disables work: no bluetoothd/callservicesd spawns (66 each in run 1).
Six swaps at 164-177s, all 1.7-2.6s offer delays, then none. The Setup boot
(scale2) reached twelve with the last two at 98ms, so the first UI frame never
arrives. PosterBoard launched; no SpringBoard/backboardd lines in the retained
msgbuf, which again starts at ~220s, now flooded by containermanagerd path
caching and 10s crash-respawns of fitcored, liveactivitiesd,
privacyaccountingd, ScreenTimeAgent, dmd, assistantd, biomed/biomesyncd.
The kernel has no BootDiagExport (boot_diag_* weak-undefined) and the logger
keeps a fixed 128 KiB, so v3 (branch commit d709df0) disables those daemons to
keep the window from being overwritten.
v3 image staging/amethyst-skip-setup-v3/Mu-amethyst-skip-setup-v3.img SHA256 6792397c0b322704cb29cc05081da605badc2c4a8ba89c115f7b1466ac8de099 (seed bb78f3a2...). Hardware untested.

### Skip Setup run 3 (v3): lock screen (2026-11-04)

User observed the iPadOS lock screen with the passcode prompt and a live,
updating clock. Log `.scratch/log-results/result-skip-setup-3` (errors0):
6 swaps 133-146s, 130s stall, InputUI/kbd/WidgetRenderer launch 271-274s,
then continuous swaps every ~2.3s (CPU presentation). Disabling the crash-looping
daemons (liveactivitiesd, privacyaccountingd, ScreenTimeAgent, dmd, assistantd,
biomed, biomesyncd) removed the v2 stall. com.apple.fitcored is the wrong label:
fitcored still crashes every 10s, harmlessly. No touch yet.
v11 image staging/amethyst-lockscreen-goodix-v11 (v10 Goodix touch kernel + v3 seed + buttons) SHA256 e10da383f6f4f67a8f8d76d6431efe254de5914b87c820078ba25f23bb90f577. Hardware untested.
PAC: all 8 cores (A520 0xd80 x4, A720 0xd81 x4) report paca pacg under Android 6.1 at EL1 (2026-11-04).

### Boot speed: single core (2026-11-04)

Every amethyst image so far booted on cpu0 only: the command line had no sheng-smp, and
the payload ADT lists 8 CPUs with cpu0 `running` (Cortex-A520, a little core) and cpu1-7
`waiting`. UniXNU-asteroids (same SM7635, same hypervisor) boots all 8 with
`sheng-smp=1 armcpu-spi-ipi=1`; this kernel carries both arguments (ArmCpu). Lock screen
took ~270-280 s (v3: first swaps 133 s, UI 271-275 s). pack-log.py `--smp` adds the two
arguments. V18 = V17 kernel and seed + --smp + --quiet-kprintf (drops
-enable_kprintf_spam; verbose console kept). Image `staging/amethyst-smp-v18`, SHA256
0b3073df5f96b5b01ae790cdc50ad231f409706e60739433a5e77649b72af0f3. Hardware untested.

V18 (8 cores) result: lock screen UI at ~152 s instead of ~272 s; first frame still 120 s.
V19 (1 MiB log, msgbuf from 39 s) timeline: kernel banner 39.0 s (0-39 s is bootloader,
UEFI, DarwinBoot and early kernel, unlogged); kexts/storage/8 CPUs online 42.3 s; launchd
45 s; launchd silent 47.9-71.3 s ("waking the CPU 14157 times/s"); daemons and per-boot
container setup 72-120 s (Data is a fresh RAM seed every boot, sheng-data-persist=0).

### V20-V21 hangs, V19 rerun, CPU governor (V23)

After V20, V20/V21 and then the unchanged V19 image (which had reached the lock screen) all stopped
right after the first two swaps; userspace logs ceased while kernel threads ran on; the phone was warm.
The root image region of unixnu hashed equal to userdata-root.json (ae3c0e19..., read in recovery),
nothing reads the export region at boot, no sleep/PMRD events. Working hypothesis: heat with 8 cores at
boot clocks (firmware thermal limits), not image content; the user is reflashing unixnu meanwhile.
V23 adds UniXNU-asteroids' CpuFreqProbe.cpp unchanged (EPSS domains 17d91000/2000/3000 = 4/3/1 CPUs,
verified in amethyst's volcano.dtsi) with sheng-cpufreq=4 (governor: 16 ms windows, schedutil margin,
4-window step-down, throttled-window count per domain in the [clocks] rows), plus the idle helper of
sheng's zzye patch as AmethystCpuIdle.c (osfmk recipe; cpu_to_processor and recount_processor_usage are
in this kernel) and a weak native_hid_input_events. pack-log --cpufreq/--cpufreq-cap. GPU probe off.
Kernel prelinked SHA256 9eb4862603bf1082e827d56720d8f7d6522b909f35f5f3b1444e1a8220079541. Image
`staging/amethyst-cpufreq-v23`, SHA256 661fd20711be3e0ce762865e6393206f9e12008ff5393403bc9bb71036cf9ed9.
Hardware untested.

V23 on hardware (result-v23): lock screen, ran 274 s with no hang. The governor scales all three domains
(d0 441-1804 MHz, d1 1286-2400, d2 1324-2304). Throttled windows d0 28 of ~10500, d1 0, d2 0: the chip
was not heat-limited, so heat is not the hang cause (the hangs stopped with no thermal change).
Touch: one "press" stuck at node 3 (row 0) at -2200 for the rest of the boot: a single-node row-0 artefact,
not a finger. The detector needs to ignore isolated single-node dips.
The user did touch the screen during V23: the stuck node held the press open, so no touch registered.

V24 (touch fix on V23): GoodixBerlinDiff.h ignores dips without a 4-neighbour at <= -300 and absorbs them
(base += d/8), resets the baseline after a press held 1500 frames, and pulls a stale baseline back on
rises >= +300 (base += d/4). check-goodix-diff.c covers the V23 node; replaying result-v20/export.bin
still gives the same 4 presses (5 lone-dip frames ignored). fr row: base= dropped, st= stuck resets,
iso= frames with a lone dip. Kernel prelinked SHA256 80e6d816...; image `staging/amethyst-touch-fix-v24`,
SHA256 e7b8d561516b96e154ed026e222c27db7ea3852c965c7218191a953485dc4cd2.

### V24 hang = stackshot with unsignalable secondaries (V25)

V24 stuck at the Apple logo (phone not warm). Every hung boot (V20 119 s, V21 278 s, V24 307 s) logs six
"cpu_signal failed in DebuggerXCallEnter" (a stackshot), then stops within ~1 s; no good boot has it. XNU
keeps SIGPdisabled on a core until it takes its first IPI (cpu_signal_handler_internal), and ArmCpu sends
that self-IPI only with armcpu-self-ipi=1, which --smp never set. So the secondaries could never be signalled
(nor idle: cpu_idle refuses with SIGPdisabled), and a stackshot ran with six cores still running.
V25 = V24 + armcpu-self-ipi=1 in --smp, and row 48 "AmethystSmp: self-ipi sigpoff=<mask> sent ipi <per core>"
(amethyst_sigp_disabled_mask in AmethystCpuIdle.c; touch rows table 49). Kernel prelinked SHA256 c23e3766...;
image `staging/amethyst-smp-ipi-v25`, SHA256 7513eea79d05592dcdc3d908e709836db64e168ef65a18d4b2db28986ea1c8d4.
V24 touch side note: 13 presses, 3 stuck resets while the user waited at the logo; check against V25.

V26 = V25 + touch baseline tracking. The user did not touch during V24: its 13 presses were false. They
grew slowly (ext -771 at 4 s to -1149 at 103 s, rows 0-2 cols 19-21 and others); V19 at 0.96 MHz shows the
same, so not the SPI clock. GoodixBerlinDiff.h now moves every node outside a press by d/64 per frame;
nodes within 2 of the press are frozen. check-goodix-diff.c adds a 5x5 area sinking 0.3 count/frame to
-900 (no press) and a finger on top (press, held 200 frames). V20 replay: same 4 presses.
Kernel prelinked SHA256 in logkit; image `staging/amethyst-touch-track-v26` (SHA256SUMS there).
V25 on hardware (result-v25): AmethystSmp self-ipi=1 sigpoff=00, IPIs taken on all 8 cores (167297..46935),
no "cpu_signal failed"; prime-core busy windows 1041 vs V23's ~2000 at the same uptime. Touch (old detector)
still 12 false presses / 4 stuck resets untouched.

V26 on hardware (result-v26): lock screen; sigpoff=00 again. No false press for the first 34 s (V24: from
6 s); 12 shallow presses (-900..-1075) at 36-95 s with nobody touching (yet unexplained; boot UI period),
then the user's five taps TL,TR,BR,BL,centre at 164-186 s, -1543..-1807, at drv-major nodes 88, 608, 643,
123, ~385. Minus 6 nodes they are (2,2) (15,2) (15,37) (2,37) (9,19): the grid starts 12 bytes later
than head+attr+log+8. V20's export agrees: its first 6 "nodes" are constants 1474,10,90,4600,0,0 (node 3
is V23's "stuck" -2200 node). Orientation: 18 axis = screen x (0 left), 40 axis = screen y (0 top).
V27 = V26 with grid offset head+attr+log+20 (GoodixBerlinFrame.h, check-goodix-frame.c updated).
Image `staging/amethyst-touch-grid-v27` (SHA256SUMS there).

V27 on hardware (result-v27): centre tap at node 381 = (9,21), -1724: grid offset confirmed. Still 16 false
presses at 58-99 s, -868..-997 (real taps -1543..-1807; V20's real presses -1077..-1242 at start, so a
1000-1150 threshold is unsafe: 1150 loses a V20 press). V28 = V27 + recorder window: ring 4096 frames,
amethyst-touch-record-until=110 (pack-log --touch-record-until), export whenever new frames exist, to
capture the false-press window. Image `staging/amethyst-touch-rec-v28`, SHA256 fda989d8....

V28 on hardware (result-v28, export 4096 frames 61.8-110 s at 85 fps, nobody touching): 14 false presses on
the device. The frames show every drive line (18 axis) jumping +-500 per frame as a whole from 62 to 95 s
(boot UI animating), tapered along the 40 axis, sometimes changing part-way along a line; quiet (+-30) after.
GoodixBerlinDiff.h now removes each line's shift (median of d*64/profile, measured 40-entry taper) and needs
an adjacent-line neighbour <= -150 (V20 bottom-edge tap: -1124 on the edge line, -200..-245 beside it).
Replays: V28 0 presses for any learning point, deepest candidate -314 (one frame); V20 realigned by 6 nodes
4/4 presses. check-goodix-diff.c adds a line-noise case (no press) and a finger inside it (press).
(Replay tools had silently used a stale header copy in /tmp/claude-1000 for a while; rerun after removal.)
V29 = V28 detector change, recorder off. Image `staging/amethyst-touch-line-v29` (SHA256SUMS there).
V29 on hardware (result-v29): no press during boot (deepest candidate -555, one frame), then exactly one
press for the user's centre tap: centroid (8.9, 18.9) of 18x40, -1302, held 168 frames. sigpoff=00, no
cpu_signal failures. Touch detection accepted; next: deliver presses to iOS as HID digitizer events.

### Touch to iOS (V30)

V30 = V29 + HID delivery (sheng-touch-hid=1, pack-log --touch-hid). The frame detector keeps a live
position each pressed frame (GoodixDiffState.live) mapped to TouchState units by goodix_diff_screen
(rows axis -> x 0..30480, columns axis -> y 0..20320, node n covers [n,n+1)); AmethystTouchEvents.cpp
publishes one finger (new id per press) under native_touch_seq every pressed frame and once on release.
AmethystButtonsHid.cpp: the real TouchState sink (from asteroids TouchState.cpp) instead of the no-op,
and asteroids' native_hid_live_checkpoint property step (Built-In, DisplayIntegrated, PrimaryUsagePage
0x0D / PrimaryUsage 4 on the event driver and its interface; backboardd drops digitizer events otherwise).
Row 49 "AmethystHid: st drv rep pub touch sent downs last" (rows table 50). compile-touch.py now also
builds AmethystButtonsHid.o. Image `staging/amethyst-touch-hid-v30` SHA256 7acbdfc2....

V30 on hardware (result-v30): IOHIDEventDriver attached to NativeHidInput ("keyboard: 232 digitizer: 10"),
Built-In/PrimaryUsage published (pub=1), 3595 touch reports submitted rc=0 over 142 presses (most starting
bottom-centre: swipe-ups). On-screen reaction to be reported by the user.
GPU: V31 = V30 + amethyst-gpu-power=1. V32 = V31 + firmware with GMU_Pool reserved (.scratch/uefi-gmu/
patch-gmu-pool.py patches the booted firmware's 4 copies of the DXE_Heap1 entry in place; pack-log --uefi-fd).
V33 = V32 kernel with probe mode 4 (32 MiB pool round trip, write-combined map); pack-log refuses mode 4
unless the firmware's PrePi carries GMU_Pool. check-gpu-power.c covers the pool test.
User on V30: "touch works then like it kinda stopped working". The kernel kept submitting reports (rc 0) to
the end and SpringBoard/backboardd did not crash. Two detector faults fit: (1) the baseline was frozen only
around the press's starting node, so a finger held after moving was tracked into the baseline (d/64 per
frame) and faded out in ~1 s; (2) release on the first weak frame could split a swipe into short taps.
V34 = V30 + live-position freeze zone, 3-frame debounced release (position held on weak frames), short-press
counter (HID row "short="). check-goodix-diff.c: hold 600 frames after a swipe stays down; one weak frame
keeps the touch; three release it. V28/V20 replays unchanged (0 false, 4/4). GPU probe off.
Image `staging/amethyst-touch-swipe-v34` SHA256 dc9e843c....
V31 on hardware (result-v31): GPU mode 1 done (stage 4, no hang). gcc_gpu_cfg_ahb 0x1 (on), memnoc
0x80000003 / dvm 0x82000001 / axi 0x82300003 (CLK_OFF), PLL vote 0x0060257f; GPUCC CX GDSCR 0x0022f801
(collapsed), hw-ctrl 0x2800010a, GX 0, cc_ahb 0, smmu vote 0x80000000 (off), gmu 0x80002001; SMMU and GPU
skipped (prerequisite: CX). Touch 102 presses / 1216 reports (old detector). sigpoff=00.
GPU firmware: amethyst's gen80300_sqe.fw and gen80300_gmu.bin hash equal to asteroids' pins (046f5aea...,
6adcbf43...); only the zap differs (9a0a7c63..., OEM-signed), so asteroids' ShengGmuSource.h inputs apply.
V34 on hardware (result-v34): user reports the same touch issue. First press 99 frames, then 68 presses of
~9 frames each (758 pressed frames / 69 presses at ~85 fps), short=4: presses end ~0.1 s in, not split.
V35 = V34 + amethyst-touch-record-press=40 (pack-log --touch-record-press): the recorder stops 40 s after
the first press, keeping ~48 s of raw frames around the touches. Image `staging/amethyst-touch-rec2-v35`.
V35 on hardware (result-v35, export around the touches): replay gives exactly the user's sequence: swipe
(0.47 s, live path x 0.45-0.51, y 0.99 -> 0.35), a 6.1 s hold steady at (0.50, 0.55), ten 0.2 s taps; the
device counted the same 12. Detection and mapping are right; the fault is on the iOS side. NativeHidInput's
notes: SpringBoard's edge gestures (home/swipe-up) need kIOHIDDigitizerEventFromEdgeTip, added by its
IOHIDEventDriver dispatch wrapper (edge::install, compiled in our object). V36 logs native_touch_edge_stats
(installed, contacts begun, at an edge, skipped) in the HID row. Image `staging/amethyst-touch-edge-v36`.
V36 on hardware (result-v36): edge hook installed (edge=1/52/3/0: 52 contacts begun, 3 at an edge, none
skipped); 936 reports rc 0. User: "when a corner is clicked it'll stop the entire touch idk or power button
can't tell". Frames kept flowing; no press detected 175-190 s, then presses again. V37 = V36 + button change
count in the HID row (btn=) + press-triggered recorder (40 s) to see whether touches after a corner tap are
in the frames. Image `staging/amethyst-touch-corner-v37`.
V37 on hardware (result-v37, export around the taps): replay = the user's sequence exactly (centre x3, corner
r16 c1, centre x3, corner r2 c38, centre x3); all 64 presses reported rc 0, edge=1/64/2/0. Button changes
(btn=7) came after touch stalled (~177-202 s, then PMRD ON_STATE and poster extensions reload: a power-button
wake), and touch resumed at ~205 s. User: tapping the bottom-right corner makes touch stop. Kernel side keeps
delivering; the stall is in iOS (InputUI, kbd and AccessibilityUIServer were spawned just before the wake).
Candidates: an iPadOS corner gesture (bottom-right finger swipe = screenshot, bottom-left = Quick Note) or
an edge-flagged contact leaving a SpringBoard system gesture recognizer engaged.
V33 on hardware (result-v33): the GMU_Pool firmware boots (lock screen). GPU mode 4 complete, stage 4:
CX GDSC powered (GDSCR f8228800, PWR_ON; hw-ctrl 80000202), SMMU vote clock on (00000001), SMMU readable
(smmu=ok), 4 writes; pool round trip over 0xE6440000 + 32 MiB: 4194304 words, 0 pattern errors, 0 zero errors.
No cpu_signal failures. Next: mode 5 (GMU SMMU context) needs NativeAdreno + the generated ShengGmuSource.h.

### GPU mode 5 (V38)

The user ran asteroids' prepare-asteroids-gmu.py --fetch in .scratch/gmu-gen (linux 734bef5 adreno sources,
amethyst firmware; zap pinned to amethyst's 9a0a7c63...): "PASS: 24 registers, fence 0x80c808c0, 6 GBIF CX
writes, 15 firmware blocks". compile-gpu.py builds NativeAdreno 5e2b23e (Memory, SmmuMemory, Vm, GmuMemory,
SmmuContext, GmuContext, Cpu) and ArmSmmu4K 83969cd (UniXNU components.toml pin; the tree's copy predates
ArmSmmu4KTablePages) into .scratch/ufs-audit/gpu. AmethystGpuProbe.cpp mode 5 = asteroids' gmuContext():
gate, uncached map of the kgsl-smmu window, NativeAdrenoGmuContextBringup(bank 1, ASID 1, slot 2, SID 5),
GFSR/FSR readback, payload addresses; row 2 "m5 gmuctx s<step> st k spans mm ttbr0 sctlr gfsr fsr hfi@".
Weak native_pmap_user_mappings stand-in (as asteroids). Image `staging/amethyst-gmu-ctx-v38`.
V38 on hardware (result-v38): GPU mode 5 reached step 7 (ready), status 0, kit program/route 0/0, 5 payload
spans, TTBR0 0x100090f8cc000 (ASID 1), SCTLR 0x167, GFSR 0, HFI at GMU VA 0x60008000. NativeAdreno's raw
mismatch 0x108 = TCR2 and SCTLR, both known MMU-500 quirks the ArmSmmu4K verifier tolerates (TBI bits read 1;
ASIDPNE reads 0, sheng int-009). Open: the GMU bank's FSR read 0x400 after bring-up (asteroids expects 0 with
the GMU halted); next build clears it (write-1-to-clear) before programming and reads it before/after.

### GPU mode 6 (V39)

Live DT pulled in recovery (.scratch/gmu-gen/.cache/amethyst/live.dtb, sha256 4c159b30...): both GPU bins,
the GMU levels (350/650 MHz), the LPDDR4X bus table, the A810 BCM policy and the cmd-db reservation
(0x81c60000 + 128 KiB) equal asteroids' scripts/data/a810-gmu-votes.json field by field; only the bins' DT order
differs, and soc0 feature code AB (2) selects the [2, 0] bin either way (pcode 1, soc_id 636 VOLCANO-1-AB,
ddr_device_type 7). Headers from prepare-asteroids-gmu-votes.py --from-data: "PASS: 2 GPU bins (10 levels, 11
levels), 3 GMU levels, A810 BCM policy, cmd-db 0x81c60000/0x20000". compile-gpu.py adds ShengGmuVotes.c (asteroids,
copied here), Sm7635Platform ShengGmuCommandDb.c/ShengGmu.c and AdrenoFirmware 96028a5 (UniXNU-amethyst/kits,
asteroids' pin). Mode 6 = asteroids' gmuPrerequisites() (read only); mode 5 clears the GMU bank FSR first.
Rows: [1] "m6 cmddb ...", [3] "m6 clk ... fsr0=" (FSR before the clear). Image `staging/amethyst-gmu-db-v39`.
V39 on hardware (result-v39): mode 6 complete. Command DB v1 magic 0c0330db, parse 0, build 0, aux gfx/cx/mx
32 bytes, sh0/mc0/acv 8, CN0 0x50030; HFI startup: 10 GPU levels (264-1050 MHz, votes 00380201..01a00d09, MX
dependency votes), 3 GMU levels (350/650 MHz), 8 bandwidths. GMU clocks before mode 7: PLL0 mode 0x100 off,
GMU/HUB RCGs off, GMU branch on, CXO on. GMU bank FSR 0 before mode 5 and 0x400 after: FSR[10:9] is the FORMAT
field (0b10 = AArch64 descriptors), not a fault bit (Linux's ARM_SMMU_CB_FSR_FAULT excludes it).
V40 = V39 + GPU modes 7-8 (asteroids' gmuClocksOn and gmuState): GMU clocks on (GPLL0 vote, PLL0 700 MHz,
hub 200 MHz, GMU 350 MHz, gpu_cc_ahb; GMU held in reset), then the GMU (0x3d37000 + 0x68000) and RSCC
(0x3d50000 + 0x10000; both match the live DT) windows mapped and the cold start preconditions read. The probe's
writes now follow asteroids' per-mode allow-list. Row 0 "m7 clk s<step> ... | m8 <gate> reset cm3 rscc".
Image `staging/amethyst-gmu-clk-v40`.
V40 on hardware (result-v40): mode 7 step 8 ok, 29 writes, PLL0 locked after 2 polls (mode e0000005, L 0x44440024
= 700 MHz config), GPLL0 vote 0061a57f, hub RCG cfg 0x505, GMU RCG cfg 0x201. Mode 8 gate ok: CM3 in reset
(reset=1), HFI 0, RSCC idle, cfg 0x4052, ITCM/DTCM readable, AO counter running (+840 ticks).
V41 = V40 + GPU modes 9-11 in one boot (asteroids' gmuColdStart and gpuGx): GMU firmware load to HFI ready,
the cold start with the HFI startup messages (perf/bandwidth tables, core start), then a GPU level vote over
HFI (default level 6, 763 MHz; amethyst-gpu-level=<n>) and OOB GPU_SET for GX. Each step only after the last
succeeded; GPU-block writes stay disallowed. compile-gpu.py adds NativeAdrenoSha256. Row 2 "m9 gmuboot ... |
m11 gx ...". Image `staging/amethyst-gmu-boot-v41`.
V41 on hardware (result-v41): modes 9-11 all pass. GMU cold start ok: firmware 88708 bytes (hash verified),
phase 7, FW_INIT_RESULT 0x100, HFI ready; HFI startup status 0, stage 8, acked stages 0xc6, 4 commands, 582 us,
health 0; GMU out of reset, AO counter running, GMU log written. GX: perf 6 / bw 5 (763 MHz) voted, OOB GPU_SET
acked after 2 polls, GX off bits 3 -> 0 (GX on), RBBM status 1. GPU-block writes not yet allowed (mode 13).

### GPU modes 12-16 (V42)

AmethystGpuProbe.cpp is replaced by asteroids' GpuPowerProbe.cpp (checkout bbf26b0, unchanged below a provenance
header; its modes 1-11 are what V31-V41 ran): sheng-gpu-power=<mode>, started from AmethystTouchSnapshot.cpp only
when that argument is present (the file defaults to 19). GpuPowerBoard.h is asteroids' copy without amethyst's
fence. compile-gpu.py adds NativeAdreno GpuContext, Ring, CpInit, Pwrup, Submit, Timer, Profile, UserClient,
Publish, UniXNU kits/AdrenoPM4, AmethystSmc.c (sheng_smc_call, from asteroids' BootWatchdogPolicy.c) and
AmethystGpuStandins.c (weak zeros for mode 18-19 symbols: asteroids' exec/swap counters and the accelerator).
V42 runs mode 16: survey + GPU SMMU context + SQE copy (12), hw_init (13), CP start + CP_MEM_WRITE (14), zap via
SCM PAS 13 with amethyst's own gen80300_zap.mbn into gpu_microcode_region 0x9ea9a000 (15), first IB and a bar
drawn into the console framebuffer (16). Image `staging/amethyst-gpu-cp-v42`.
V42 on hardware (result-v42): modes 12-16 all pass; the user saw the bar before the lock screen. Survey clean
(fuse 0, RBBM 1); GPU SMMU context bank 0 ready (oas 36, mismatch 0); SQE 85840 bytes copied, 0 differ; hw_init
step 9, 203 writes (1 mismatch, as asteroids); CP init fence 1, CP_MEM_WRITE 0x05e9a810 ok, power-up list 1480
bytes; zap: PAS 13 supported, amethyst's own gen80300_zap.mbn (meta 0xfcc, 1 load) init_image/mem_setup/
auth_and_reset status 0, CP_SET_SECURE_MODE 0 then 0x05ec0ff1 ok, trust_cntl 0; first IB 0x1b810014 ok; bar over
the 1220x2712 console (rows 1808-1855) drawn, pixels read back red/green/blue, 600 redraws in 2 min, 0 failed.
SMMU bank 0 FSR 0x400 throughout = FORMAT field (not a fault). No cpu_signal failures.
V43 = the V42 kernel with sheng-gpu-power=17: fences and timer on the live ring, native_adreno_publish
(NativeAdrenoGpu service), two in-kernel client rounds (BOs, map, submit, wait, close) and a 32-submission burst;
results in BootLog's [gpu uc] rows. Image `staging/amethyst-gpu-uc-v43`.
V43 on hardware (result-v43): mode 17 complete. Fences and the timer attached (always-on counter +22464 per ms:
the 19.2 MHz/... CP counter runs), native_adreno_publish 0: NativeAdrenoGpu service, chip 44010000, GMEM 0x90000,
GPU VA 0x100000000+; user-client test 115/115 steps, 4/4 rounds (words 0badc0de feed000n 600d0a81 match), a burst
of 32 submissions matched 32/32, 34 jobs done, 3 connections opened and closed. The bar still drawn first.
V45 = V44 kernel (accelerator) + gpu-addon CI run 41932148833 (addon 1d83155c..., owner trust 2c77eb8b...), sheng-gpu-power=19 (pack-log choices raised to 19). Untested.
V45 on hardware (result-v45): mode 19 ran: bar drawn once, NativeAdrenoGpu published, Metal accelerator
published and registered; backboardd, SpringBoard, mediaserverd, WidgetRenderer and CollectionsPoster read its
properties, MetalPluginName read 0 times; addon files -1 (never installed): the kernel's addon install/mount runs
only with sheng-gpu-addon=1. UI stayed on CPU rendering. One fence-wait timeout (sheng-gpu-irq default 1).
V46 = V45 + sheng-gpu-addon=1 sheng-gpu-irq=0 (pack-log adds both with --gpu-addon). Image staging/amethyst-metal-v46.
V47 = V46 + AppleKeyStore stand-in (sheng-aks=1): UniXNU drivers/AppleKeyStoreState/NativeKeyStoreEvents.cpp (late
registration) + asteroids KeyStoreProbe.cpp, run from the touch snapshot hook. Answers libaks/MobileKeyBag "no keybag,
no passcode, unlocked since boot" so the lock screen dismisses on swipe. Kernel kernel-aks-v47 (prelinked 218a929f...),
image staging/amethyst-aks-v47 (4187c5df...). Untested.
V47 on hardware (.scratch/result-v47): AKS stand-in live (opens=111 calls=4042); Preferences (Settings) launched, so the
lock screen was passed. GPU addon installed this time (A files=6 wr=6 mount=5/0). Metal askers: backboardd, SpringBoard,
mediaserverd, WidgetRenderer, CollectionsPoster, PhotosPosterProvider, Preferences, Spotlight. SpringBoard (s1) and
Preferences (s2) opened GPU devices; 53 submissions/65 IBs, all fences matched, 0 lost. WidgetRenderer, CollectionsPoster,
PhotosPosterProvider: Vulkan -3 (no-device); mediaserverd: no-cachedir. backboardd asked but has no GPU device (s-).
Method 6: 1056 calls, 981 failures (unexamined).
V47 cause of CPU compositing: msgbuf "Sandbox: backboardd(53) deny(1) iokit-open-user-client AGXDeviceUserClient", open
res e00002e2, plugin "PUB no device: Vulkan -3" (asteroids T62): GpuPowerProbe set NativeSandboxGrantProcess="*" and this
kernel's zzw-native-adreno-sandbox-grant compares entries as exact names. SpringBoard/Preferences opened only because their
profiles allow the class.
V48 = V47 + NativeSandboxGrantProcess="backboardd" + touch corner guard (points within 1500 units of an x and a y edge are
moved out of the corner square; amethyst-touch-corner=N, 0 off; counter cn= in the AmethystHid row) against iPadOS's
bottom-left/right finger corner gestures freezing touch. Kernel kernel-grant-v48 (53d8d926...), staging/amethyst-grant-v48.
V48 on hardware (.scratch/result-v48): backboardd now holds GPU slot 0; render server on the GPU (596 command buffers,
504 pipelines, 16.5 s compiling). Screen black: the original IOMobileFramebufferLegacy logged 6352 "verify_swap failed"
(NativeLegacyDisplay::validateSwap refused the GPU-composited swaps); only 3 copies. Touch thread ran, no presses.
V49 = V48 + row 48 "Swap a= r= why=..." (rejection reason, layer state/options/size/rects, or surface w/h/format/planes/
stride/alloc/bytes for why=4) replacing the SMP row once a swap was rejected. staging/amethyst-swapdiag-v49.
V49 on hardware (.scratch/result-v49): black as V48. Row 48: "Swap a=1 r=13324 why=4 sf=1220x2712 f=62336138 ('b3a8') p=2
st=1280 al=16838656": backboardd composited into Apple-compressed two-plane framebuffers, which validateSwap (plain BGRA or
one-plane &BGA only) refuses. Touch: cn=1 (one corner touch moved), 2 downs.
V50 = V49 kernel + Data seed skip-setup-seed-v4 (skip-setup-seed branch 952ad29, CI run 41995224691): CAEnableDeepFramebuffer,
CAEnableFramebufferCompression, CAEnableIOSurfaceCompression false in backboardd and .GlobalPreferences for mobile and root
(as UniXNU add-gpu-plugin-data.py --publish). staging/amethyst-plainfb-v50.
V50 on hardware (.scratch/result-v50): picture on screen, GPU blur, no swap rejections; very slow. GPU frames 10 ms mean
(G commit-to-complete), but every display swap took ~170 ms offer-to-done (swap timeline W484-W523): the CPU copy of each
1220x2712 GPU frame into the GOP framebuffer (5-6 fps). backboardd restarted once (~267 s; EXC_RESOURCE wakeups corpse at 266 s).
Touch: the kernel delivered every press to the end (rep 2681, 69 downs, last ones bottom-centre swipes); the "touch stopped" and
garbled picture are on the iOS/GPU side.
V51 = V50 + --fb-gpu (sheng-fb-gpu=1 sheng-vsync=120, as asteroids): the GPU copies each frame to the panel through the hand-off
presenter instead of the CPU. staging/amethyst-fbgpu-v51.
V51 on hardware (.scratch/result-v51): smooth (GPU hand-off: swaps offer 0-7 ms, done 6-50 ms, against ~170 ms in V50) but text
and icons garbled; froze while moving the UI. No panic text in msgbuf; the log stops at 176 s uptime. Last swap W293 never
retired; [gpu stall] fence=611, [gpu unstick] kick=3 (the GPU hung; three kicks did not recover it); backboardd opened the GPU
13 times (the render server restarted). metal2vulkan warned "color attachment 0 ... loaded with DontCare" (tiles outside the
draws undefined). Our NativeAdreno 5e2b23e is asteroids' pin and includes 4063b92 (WC system BO clean); asteroids' GPU args
differ only by sheng-gpu-dcvs=1 min 6 max 7. Suspect next: the add-on (plugin/Turnip) built with UniXNU pinned at f2ce6cce45
against asteroids' own T63+ stack.
V52 = V51 + add-on rebuilt as asteroids 6370e66 builds it (UniXNU-amethyst-gpu gpu-addon d468023, CI run 41999151153): UniXNU
asteroids-dev bd4766ae, drivers/Adreno740 38227e7 (header-only: SetPriority/Note declarations), asteroids' current
AsteroidsMetalPlugin.mm. addon c8d14d93..., owner trust 9be77135... (.scratch/gpu-addon-ci-v2). Kernel unchanged (v49).
staging/amethyst-addon2-v52.
V52 on hardware (.scratch/result-v52, uptime 326 s, 162 rounds, 0 write errors): text and icons render correctly and moving the UI no
longer freezes (user). Swaps steady at ~15 ms intervals, offer 0-3 ms, done 10-11 ms. [gpu unstick] kick=24 recovered every stall; 2860
submissions, fences matched. GPU clients: backboardd s0, SpringBoard s1, Spotlight s2; mediaserverd no-cachedir, WidgetRenderer,
CollectionsPoster, PhotosPosterProvider Vulkan -3. Touch: 2035 reports / 37 downs, cn=1 at ~313 s; two more downs were delivered after
it, but iPadOS stopped reacting to touches after the bottom-corner touch (user). The kernel side keeps delivering; next: turn off the
iPadOS finger corner gestures (Screenshot / Quick Note) in the Data seed.

### Touch, GPU free and brightness (V53-V60, 2026-11-07)

All with the V52 add-on (.scratch/gpu-addon-ci-v2) except V53, packed as V52 with --touch-record-button from V55 on; results in
.scratch/result-v5N. Frames replayed through GoodixBerlinDiff.h with the recorder export (export.bin).
V53 (amethyst-oslog-v53, c70a240e...): SpringBoard's unified log through the export channel (springboard-oslog branch, add-on ci-v3);
the export held 0 entries. Not pursued.
V54 (amethyst-touchids-v54, aac4bf0a...): contact identifiers cycle 1..8 instead of 1..200. 303 touches, touch never died (it had
after ~30 presses since V30).
V55 (amethyst-touchrec-v55, 149232a1...): corner guard removed (swipe up to home was hard); button-stopped frame recorder. User: after
several Search pull-downs a tap on Settings opened Search (an old touch continued).
V56 (amethyst-inrange-v56, 257aa54c...): NativeHidInput reports In Range (patches/ShengHidInput/0001). The Search/Settings bug was gone.
Spamming Spotlight froze the device: [gpu stall] smmu 80000402 far 31f46000 (a translation fault; every earlier boot showed 0x400),
fence 2191 never completed, [gpu recovery] hangs 30 restarts 0 (no restart hook). Recording: swipes up from the bottom jumped back to
the bottom line as the finger lifted (41 jumps in 42 presses), so scrolling stuttered.
V57 (amethyst-gpufree-v57, 1e74358b...): NativeAdreno deferred BO frees. Not booted on its own.
V58 (amethyst-follow-v58, a379a3ad...): V57 + the detector follows its finger only (0 jumps on V56's frames). [bo deferred]
freed=1638 reaped=1637 waiting=1 map-waits=16 wait-timeouts=0; FSR 0x400 throughout; no hang in 429 s. User: taps slide a little;
recording: 16 of 29 taps wandered 10-47 pt.
V59 (amethyst-panel-v59, da52321c...): V58 + amethyst-panel=3 (DSI0 DCS brightness service). Not booted on its own.
V60 (amethyst-tap-v60, fbfd6f43...; kernel kernel-tap-v60, 572f3dcc...): V59 + tap slop and depth-past-floor centroid (V58's taps
moving >10 pt: 7 of 37). User: taps, scrolling, the brightness slider and lock/unlock all work.
The V57-V60 kernels' NativeAdreno objects are 5e2b23e with 26c1da7's user client (26c1da7 itself is on 38227e7, which adds only
ABI declarations).
