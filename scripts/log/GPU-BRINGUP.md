# Adreno 810 (GPU) bring-up on amethyst

Goal: Metal through the GPU instead of CPU presentation. rootstockos/UniXNU-asteroids (Nothing Phone 3a,
the same SM7635 "volcano" SoC) already runs it on iPadOS 17: the render server composites on the A810
("window server ready in 1.9 s instead of 30.8 s"). amethyst follows its staged path.

## Sources

| Piece | Source | amethyst |
| --- | --- | --- |
| Staged power sequence | asteroids `package/GpuPowerBoard.h` (e12eb61), copied unchanged | modes 1-11 compiled; 12+ fenced (`GPB_WITH_A810_HWINIT`) until NativeAdrenoA810HwInit.h is generated |
| Probe thread | asteroids `package/GpuPowerProbe.cpp` | `AmethystGpuProbe.cpp`, modes 1-3 only (`amethyst-gpu-power`) |
| GMU cold start, cmd-db | rootstockos/UniXNU-sm7635-common `kits/Sm7635Platform` | not yet |
| Driver, ring, user client | rootstockos/NativeAdreno (gen8 fork of UniXNU Adreno740) | not yet |
| Metal plugin + Turnip | asteroids `package/AsteroidsMetalPlugin.mm`, `gpu-addon.yml` (macOS CI) | not yet |
| Firmware | amethyst vendor: `vendor/firmware/gen80300_sqe.fw`, `gen80300_gmu.bin`; `odm/firmware/gen80300_zap.mbn` (Xiaomi-signed) | present in the EvolutionX vendor tree |

## Board facts re-checked for amethyst

From the volcano.dtsi that amethyst ships (`.scratch/amethyst-hardware-dts/qcom`): gcc@100000,
gpucc@3d90000 (0x9800), kgsl-smmu@3da0000 (0x40000), kgsl-3d0@3d00000, `&gpu_cc_cx_gdsc` reg 0x3d99080 with
hw-ctrl 0x3d99094 (volcano.dtsi overrides pineapple-gdsc.dtsi's 0x3d99108/0x3d99168), clk-dis-wait 8,
gpu_microcode_region@9ea9a000 (0x2000, the zap region; amethyst does not override it). All equal asteroids'.

GMU pool: asteroids' UEFI reserves `GMU_Pool` 0xE6440000 + 32 MiB from the stock `DXE_Heap1`. amethyst's
Mu-Silicium map (`uefi-amethyst/Platforms/Xiaomi/amethystPkg/Library/MemoryMapLib/MemoryMapLib.c`) has the
same `DXE_Heap1` 0xE6440000 + 0x024C0000, so the same carve-out applies. It needs a UEFI rebuild; required
from mode 4 on.

## Steps

1. Modes 1-3 (this build): GCC/GPUCC/SMMU observation, AHB enable, CX GDSC power-up, SMMU clocks.
   Host test `check-gpu-power.c`. Results in BootLog's `[gpu uc]` rows; `stage` names the block in use
   (0x10+window, 0x30 CX power-up, 4 done).
2. UEFI with the GMU pool; mode 4 (pool round trip), 5 (GMU SMMU context).
3. Sm7635Platform + firmware arrays (prepare-asteroids-gmu.py): modes 6-11 (GMU clocks, cold start, GX).
4. NativeAdreno + generated A810 hw-init: modes 12-16 (CP start, zap, first IB).
5. User client and Metal discovery (17-19), the add-on with the Metal plugin and Turnip (macOS CI).

## Results

V22 (`staging/amethyst-gpu-probe-v22`, amethyst-gpu-power=1, read only): kernel prelinked SHA256
36ef7a9e7b6e4e41b9846b13c3426e3853d5fcd71d810367df533821cd9b7f64, image SHA256
0cc5fc824ca42f4f7d5b383e0ec96ebb9cfdd32a08bd1696af7029160ee183ce. Hardware untested.
V22 also raises the Goodix SPI clock from 0.96 to 4.8 MHz (DT spi-max-frequency 15 MHz): every frame
read is a busy wait, so V17-V21 kept a core spinning about 14 ms per frame.
V31 (mode 1, hardware): GCC/GPUCC readable, CX collapsed, SMMU and GPU skipped as designed.
V33 (mode 4 + patched firmware .scratch/uefi-gmu/amethyst-gmu-pool.fd, hardware): CX powered, SMMU readable,
GMU pool 32 MiB round trip clean. Steps 1-2 of the plan are done; step 3 needs ShengGmuSource.h
(asteroids' prepare-asteroids-gmu.py applies unchanged: amethyst's sqe/gmu firmware equal asteroids' pins).
V38 (mode 5, hardware): the GMU's SMMU context bank built on the pool, ready (step 7), payloads mapped
(HFI at 0x60008000). FSR 0x400 on the GMU bank to be explained. Next: mode 6 (read-only GMU clocks and AOP
command DB; needs prepare-asteroids-gmu-votes.py output and Sm7635Platform's ShengGmuCommandDb.c).
V39-V41 (hardware): mode 6 command DB + HFI table, mode 7 GMU clocks, mode 8 GMU state, mode 9-10 GMU cold
start with HFI startup, mode 11 GX on at 763 MHz. Step 3 of the plan is done. Next: step 4, NativeAdreno's A810
hw init (GPB_WITH_A810_HWINIT, NativeAdrenoA810HwInit.h), modes 12-16: SQE, CP start, zap (amethyst's zap is
OEM-signed: 9a0a7c63..., different from asteroids'), first IB.
V42 (modes 12-16, hardware): CP running, zap accepted by TrustZone (amethyst's OEM-signed zap), first IBs, the GPU
draws a bar on the panel (600 redraws, 0 failures). Step 4 of the plan is done. Left: step 5, NativeAdreno's user
client and Metal discovery (modes 17-19) and the add-on with the Metal plugin and Turnip (asteroids gpu-addon.yml,
macOS CI).
V43 (mode 17, hardware): NativeAdrenoGpu published for user space; in-kernel client 115/115 steps, burst 32/32.
Left for Metal: mode 19 (NativeAdreno's accelerator for the original Metal discovery) and the GPU addon (the
gpu-addon branch's CI build) in the payload with its owner trust cache.
