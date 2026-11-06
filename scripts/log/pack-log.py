#!/usr/bin/env python3
"""Build a RAM boot image that turns on the UFS diagnostic log (sheng-log=1).

This is the answer to the real problem: Amethyst has no usable serial console
(its device tree has no boot-console/uart node, and SM7635 reaches eUSB2 through
the PMIC rather than a DWC3 block), so every boot so far could only be inspected
by photographing the screen. sheng's BootLog writer stores the kernel message
buffer into the NativeUfs log window, which now lives in the tail of the unixnu
partition -- so a full boot log can be read back over ADB, and Android's
userdata is never written.

Changes exactly one variable relative to the hardware-proven owner-union
baseline: Payloads/kernel.macho is the prelinked kernel built with BootLog.c,
plus the single boot argument sheng-log=1. Every other file and every other boot
argument is byte-identical to the baseline.
"""
import argparse
import gzip
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[3]
KIT = Path(__file__).resolve().parent
# Scratch space for staged images; never /tmp, so intermediate files survive a reboot.
AUDIT = ROOT / '.scratch/ufs-audit'
SOURCE = ROOT / 'staging/amethyst-owner-union/Mu-amethyst-owner-union.img'
KERNEL = KIT / 'kernel-log-prelinked.macho'
OUT = ROOT / 'staging/amethyst-log'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', type=Path, default=KERNEL)
parser.add_argument('--out', type=Path, default=OUT)
parser.add_argument('--data-seed', type=Path,
                    help='explicit replacement packed RAM Data seed; never modifies the original')
parser.add_argument('--display-probe', action='store_true',
                    help='enable the separately linked legacy display publication probe')
parser.add_argument('--display-pm', action='store_true',
                    help='enable logical GOP power management (requires --display-probe)')
parser.add_argument('--display-unit-scale', type=int, choices=(1, 2, 3),
                    help='explicit GOP pixels per logical point (requires --display-probe)')
parser.add_argument('--display-validate', action='store_true',
                    help='enable the bounded GOP swap validator (requires --display-pm)')
parser.add_argument('--display-cpu-present', action='store_true',
                    help='enable transfer and retirement together (requires --display-validate)')
parser.add_argument('--touch-snapshot', action='store_true',
                    help='opt-in read-only clock/pin/SE0 snapshot; requires matching linked probe')
parser.add_argument('--touch-clock-vote', action='store_true',
                    help='opt-in additive QUP0 clock votes, no SPI/reset/rails (requires --touch-snapshot)')
parser.add_argument('--touch-dma-preflight', action='store_true',
                    help='allocate retained buffers and plan only; requires --touch-clock-vote')
parser.add_argument('--touch-loopback', action='store_true',
                    help='run one DMA loopback after the route commit; requires --touch-dma-preflight')
parser.add_argument('--gpu-addon', type=Path,
                    help='the gpu-addon CI output asteroids-metal.addon (NGPUADD2): Payloads/kernel.macho.gpuaddon + ADT GpuAddon')
parser.add_argument('--owner-trust', type=Path,
                    help='the gpu-addon CI owner-trust.bundle (owner module + the original modules); requires --gpu-addon')
parser.add_argument('--fb-gpu', action='store_true',
                    help='V51: the GPU copies each frame to the panel (sheng-fb-gpu=1) with a 120 Hz vsync timer (sheng-vsync=120), as asteroids boots')
parser.add_argument('--aks', action='store_true',
                    help='AppleKeyStore stand-in (sheng-aks=1): no passcode, unlocked; the lock screen dismisses without one')
parser.add_argument('--uefi-fd', type=Path,
                    help='replace the 3 MiB firmware body (e.g. .scratch/uefi-gmu/amethyst-gmu-pool.fd: GMU_Pool reserved)')
parser.add_argument('--shim-from', type=Path,
                    help='reuse the 192-byte boot shim of an earlier packed image (when the donor BootShim.S is gone); '
                         'its encoded payload size and total size are still asserted')
parser.add_argument('--touch-goodix', action='store_true',
                    help='after a passing loopback: rails, reset, SPI pins, one Goodix identity read; requires --touch-loopback')
parser.add_argument('--touch-events', action='store_true',
                    help='after a valid Goodix identity: event poller with live rows 46-47; requires --touch-goodix')
parser.add_argument('--touch-record-until', type=int, default=0,
                    help='record Goodix frames (amethyst-touch-record=1) and stop N s after touch start; export read by pull-log.py')
parser.add_argument('--touch-hid', action='store_true',
                    help='deliver frame-detector presses to iOS (sheng-touch-hid=1: NativeHidInput digitizer); requires --touch-frames and --buttons')
parser.add_argument('--touch-record-button', action='store_true',
                    help='record Goodix frames until a hardware button goes down, then export the ~48 s before it once')
parser.add_argument('--panel', type=int, default=0, choices=range(0, 5),
                    help='AMOLED panel over DSI0 DCS (amethyst-panel=N, AmethystPanel.cpp): 1 read-only, 2 brightness test, 3 iOS brightness service, 4 off/on test')
parser.add_argument('--touch-record-press', type=int, default=0,
                    help='record Goodix frames and stop N s after the first detected press')
parser.add_argument('--touch-frames', action='store_true',
                    help='after a valid Goodix identity: read host-computing frames (amethyst-touch-events=2); requires --touch-goodix')
parser.add_argument('--smp', action='store_true',
                    help='start the secondary CPUs: sheng-smp=1 armcpu-spi-ipi=1 armcpu-self-ipi=1 (each core takes a first IPI so XNU will signal it)')
parser.add_argument('--msgbuf', type=int, default=0,
                    help='kernel message buffer bytes (msgbuf=N); pair with a BootLog whose message area holds it')
parser.add_argument('--gpu-power', type=int, default=0, choices=tuple(range(20)),
                    help='Adreno 810 staged power probe (amethyst-gpu-power): 1 reads only, 2 + AHB enable, 3 + CX power-up, '
                         '4 + GMU pool round trip (needs --uefi-fd with GMU_Pool reserved), 5 + GMU SMMU context on the pool, 6 + read-only GMU clocks and command DB, 7 + GMU clocks on, 8 + GMU/RSCC state read, 9 GMU firmware to HFI ready, 10 + HFI startup, 11 + GX on')
parser.add_argument('--cpufreq', type=int, default=0, choices=tuple(range(20)),
                    help='EPSS CPU frequency control (sheng-cpufreq): 1 read only, 3 vote max non-turbo, 4 governor')
parser.add_argument('--cpufreq-cap', type=int, default=0, help='highest MHz the governor may vote (sheng-cpufreq-cap)')
parser.add_argument('--buttons-survey', action='store_true', help='read-only Amethyst SPMI arbiter survey')
parser.add_argument('--quiet-kprintf', action='store_true', help='remove explicit kprintf spam flag; retain durable logging')
parser.add_argument('--home-once', action='store_true', help='one native Home press/release after HID attachment; requires --buttons')
parser.add_argument('--buttons', action='store_true', help='native Amethyst power/volume HID candidate')
parser.add_argument('--buttons-identity', action='store_true', help='bounded PMIC identity reads; requires --buttons-survey')
args = parser.parse_args()
if args.home_once and not args.buttons:
    parser.error('--home-once requires --buttons')
if args.buttons_identity and not args.buttons_survey:
    parser.error('--buttons-identity requires --buttons-survey')
if args.touch_frames and (args.touch_events or not args.touch_goodix):
    parser.error('--touch-frames requires --touch-goodix and excludes --touch-events')
if args.touch_events and not args.touch_goodix:
    parser.error('--touch-events requires --touch-goodix')
if args.touch_goodix and not args.touch_loopback:
    parser.error('--touch-goodix requires --touch-loopback')
if args.touch_loopback and not args.touch_dma_preflight:
    parser.error('--touch-loopback requires --touch-dma-preflight')
if args.touch_dma_preflight and not args.touch_clock_vote:
    parser.error('--touch-dma-preflight requires --touch-clock-vote')
if args.touch_clock_vote and not args.touch_snapshot:
    parser.error('--touch-clock-vote requires --touch-snapshot')
if args.display_pm and not args.display_probe:
    parser.error('--display-pm requires --display-probe')
if args.display_unit_scale is not None and not args.display_probe:
    parser.error('--display-unit-scale requires --display-probe')
if args.display_validate and not args.display_pm:
    parser.error('--display-validate requires --display-pm')
if args.display_cpu_present and not args.display_validate:
    parser.error('--display-cpu-present requires --display-validate')
KERNEL, OUT = args.kernel, args.out
DEST = OUT / ('Mu-' + OUT.name + '.img')
BASE_SHA256 = '73c478d5bf60130761db524fbb37a8ce7bcb4bb6a64241a3f9ea2da3a50075e4'

# A bootable kernel MUST carry the prelinked kext segments. Without this guard a
# plain linker output silently produces a kernel with no drivers, which stalls
# before the text console -- the fault that wasted three earlier device boots.
assert KERNEL.stat().st_size > 115 * 1024 * 1024, (
    f'{KERNEL.name} is {KERNEL.stat().st_size} bytes: not prelinked. '
    f'Run prelink-candidate.py first.')
assert b'__PRELINK_TEXT' in KERNEL.read_bytes()

b = SOURCE.read_bytes()
assert hashlib.sha256(b).hexdigest() == BASE_SHA256, 'baseline image changed'
size = struct.unpack_from('<I', b, 8)[0]
d = zlib.decompressobj(31)
raw = d.decompress(b[4096:4096 + size]) + d.flush()
assert d.eof
prefix = 192 + 0x300000

# The shim and UEFI half reuse the payload size from the baseline, so a mismatch
# here means the image is not what this script was written against.
OUT.mkdir(exist_ok=True)
if DEST.exists():
    DEST.unlink()
    (OUT / 'SHA256SUMS').unlink(missing_ok=True)

with tempfile.TemporaryDirectory(dir=AUDIT, prefix='log-pack-') as tempdir:
    temp = Path(tempdir)
    old = temp / 'old.img'
    old.write_bytes(raw[prefix:])

    def get(image, path):
        return subprocess.check_output(['mtype', '-i', str(image), path])

    config = get(old, '::/EFI/UniXNU/unixnu.toml')
    assert b'sheng-log=1' not in config, 'baseline already enables the log'
    # Add the one flag. The driver reads it with PE_parse_boot_argn; the log
    # window then appears in unixnu's tail (NativeUfs kLogReserveBlocks).
    assert config.count(b'sheng-log=0') == 1, 'expected one baseline log setting'
    config = config.replace(b'sheng-log=0', b'sheng-log=1')
    assert config.count(b'sheng-log=') == 1, 'duplicate log settings'
    assert config.count(b'sheng-log=1') == 1
    if args.display_probe:
        assert config.count(b'offline-apfs-load=1') == 1
        for flag in (b'offline-mobile-graphics-load', b'offline-legacy-register', b'offline-legacy-phase', b'offline-legacy-gop'):
            assert flag + b'=' not in config, 'display flag already set'
        config = config.replace(b'offline-apfs-load=1',
                                b'offline-apfs-load=1 offline-mobile-graphics-load=1 '
                                 b'offline-legacy-register=1 offline-legacy-phase=4 offline-legacy-gop=1')
    if args.display_pm:
        assert b'offline-legacy-pm=' not in config, 'power setting already supplied'
        config = config.replace(b'offline-legacy-gop=1',
                                b'offline-legacy-gop=1 offline-legacy-pm=1')
    if args.display_unit_scale is not None:
        assert b'offline-legacy-unit-scale=' not in config, 'scale setting already supplied'
        config = config.replace(b'offline-legacy-gop=1', b'offline-legacy-gop=1 '
                                + f'offline-legacy-unit-scale={args.display_unit_scale}'.encode())
    if args.display_validate:
        assert b'offline-legacy-validate=' not in config, 'validation setting already supplied'
        config = config.replace(b'offline-legacy-gop=1',
                                b'offline-legacy-gop=1 offline-legacy-validate=1')
    if args.display_cpu_present:
        for flag in (b'offline-legacy-transfer=', b'offline-legacy-cpu-present='):
            assert flag not in config, 'presentation setting already supplied'
        config = config.replace(b'offline-legacy-gop=1',
                                b'offline-legacy-gop=1 offline-legacy-transfer=1 offline-legacy-cpu-present=1')
    if args.quiet_kprintf:
        assert config.count(b' -enable_kprintf_spam') == 1
        config=config.replace(b' -enable_kprintf_spam',b'')
    if args.buttons:
        assert b'amethyst-buttons=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-buttons=1')
    if args.home_once:
        assert b'amethyst-home-once=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-home-once=1')
    if args.buttons_survey:
        assert b'amethyst-buttons-survey=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-buttons-survey=1')
    if args.buttons_identity:
        assert b'amethyst-buttons-identity=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-buttons-identity=1')
    if args.touch_snapshot:
        assert b'amethyst-touch-snapshot=' not in config, 'touch snapshot setting already supplied'
        mode=2 if args.touch_clock_vote else 1
        config = config.replace(b'sheng-log=1', b'sheng-log=1 '+f'amethyst-touch-snapshot={mode}'.encode())
    if args.touch_dma_preflight:
        assert b'amethyst-touch-dma-preflight=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-dma-preflight=1')
    if args.msgbuf:
        assert b'msgbuf=' not in config and 131072 <= args.msgbuf <= 1048576
        config=config.replace(b'sheng-log=1',b'sheng-log=1 msgbuf=%d' % args.msgbuf)
    if args.cpufreq:
        assert b'sheng-cpufreq' not in config
        extra=b' sheng-cpufreq=%d' % args.cpufreq + (b' sheng-cpufreq-cap=%d' % args.cpufreq_cap if args.cpufreq_cap else b'')
        config=config.replace(b'sheng-log=1',b'sheng-log=1'+extra)
    if args.gpu_power >= 4:
        # The pool test writes 32 MiB at 0xE6440000: only safe when the firmware keeps it out of the map.
        assert args.uefi_fd and b'GMU_Pool' in args.uefi_fd.read_bytes()[:0xe000], 'mode 4 needs a firmware reserving GMU_Pool'
    if args.gpu_power:
        # V42: asteroids' GpuPowerProbe.cpp reads sheng-gpu-power (modes 1-19; 12-16 SQE, CP, zap, first IB)
        assert b'sheng-gpu-power=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 sheng-gpu-power=%d' % args.gpu_power)
    if args.smp:
        assert b'sheng-smp=' not in config and b'armcpu-spi-ipi=' not in config and b'armcpu-self-ipi=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 sheng-smp=1 armcpu-spi-ipi=1 armcpu-self-ipi=1')
    if args.fb_gpu:
        # V50: the CPU copy of each 1220x2712 GPU frame took ~170 ms (5-6 fps). asteroids: sheng-fb-gpu=1, sheng-vsync=120.
        assert args.gpu_addon and b'sheng-fb-gpu=' not in config and b'sheng-vsync=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 sheng-fb-gpu=1 sheng-vsync=120')
    if args.aks:
        assert b'sheng-aks=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 sheng-aks=1')
    if args.gpu_addon:
        # V46: the kernel installs and mounts the addon only with sheng-gpu-addon=1 (kern_exec.c native_gpu_addon_prepare);
        # sheng-gpu-irq=0 is asteroids' board default (fence waits spin).
        assert b'sheng-gpu-addon=' not in config and b'sheng-gpu-irq=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 sheng-gpu-addon=1 sheng-gpu-irq=0')
    if args.touch_hid:
        assert args.touch_frames and args.buttons and b'sheng-touch-hid=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 sheng-touch-hid=1')
    if args.touch_record_button:
        assert args.touch_frames and not args.touch_record_until and not args.touch_record_press and b'amethyst-touch-record' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-record=1 amethyst-touch-record-button=1')
    if args.panel:
        assert b'amethyst-panel=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-panel=%d' % args.panel)
    if args.touch_record_press:
        assert args.touch_frames and not args.touch_record_until and b'amethyst-touch-record' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-record=1 amethyst-touch-record-press=%d' % args.touch_record_press)
    if args.touch_record_until:
        assert args.touch_frames and b'amethyst-touch-record' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-record=1 amethyst-touch-record-until=%d' % args.touch_record_until)
    if args.touch_frames:
        assert b'amethyst-touch-events=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-events=2')
    if args.touch_events:
        assert b'amethyst-touch-events=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-events=1')
    if args.touch_goodix:
        assert b'amethyst-touch-goodix=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-goodix=1')
    if args.touch_loopback:
        assert b'amethyst-touch-loopback=' not in config
        config=config.replace(b'sheng-log=1',b'sheng-log=1 amethyst-touch-loopback=1')

    adt = get(old, '::/EFI/UniXNU/Payloads/kernel.macho.adt')
    trust = get(old, '::/EFI/UniXNU/Payloads/kernel.macho.trustcache')
    seed = get(old, '::/EFI/UniXNU/Payloads/scratch-data.img') if b'scratch-data.img' in config else None
    if args.data_seed:
        if seed is None:
            raise SystemExit('baseline has no RAM Data seed to replace')
        import importlib.util
        packed = ROOT / 'UniXNU/drivers/DarwinBoot/Test/packed_ramdisk.py'
        spec = importlib.util.spec_from_file_location('packed_ramdisk', packed)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        seed = args.data_seed.read_bytes()
        logical, _ = module.regions(seed)
        if logical != 2 * 1024**3:
            raise SystemExit('replacement seed must preserve the 2 GiB logical size')
    baseline_kernel = get(old, '::/EFI/UniXNU/Payloads/kernel.macho')
    assert baseline_kernel != KERNEL.read_bytes()

    fat = temp / 'new.img'
    with fat.open('wb') as f:
        f.truncate(160 * 1024 ** 2)
    subprocess.run(['mformat', '-F', '-i', str(fat), '-v', 'AMETHYSTESP', '::'], check=True)
    for p in ['::/EFI', '::/EFI/UniXNU', '::/EFI/UniXNU/Payloads']:
        subprocess.run(['mmd', '-i', str(fat), p], check=True)
    if args.gpu_addon or args.owner_trust:
        # V45: the Metal plugin addon. The trust bundle must be the owner module followed by our baseline's modules byte for
        # byte (the CI builds it from the same iPadOS 17.7.11 original, sha 7416ca4f...); the ADT gains /chosen/memory-map
        # GpuAddon (adt-add-gpuaddon.py), which DarwinBoot fills for Payloads/kernel.macho.gpuaddon.
        assert args.gpu_addon and args.owner_trust, '--gpu-addon and --owner-trust go together'
        addon = args.gpu_addon.read_bytes(); owner = args.owner_trust.read_bytes()
        assert addon[:8] == b'NGPUADD2', 'not an NGPUADD2 addon'
        count = struct.unpack_from('<I', trust)[0]; first = struct.unpack_from('<I', trust, 4)[0]
        ocount = struct.unpack_from('<I', owner)[0]; ofirst = struct.unpack_from('<2I', owner, 4)[1]
        assert ocount == count + 1 and owner[ofirst:] == trust[first:], 'owner trust bundle does not carry the baseline modules'
        trust = owner
        spec = importlib.util.spec_from_file_location('adtadd', Path(__file__).resolve().parent / 'adt-add-gpuaddon.py')
        adtadd = importlib.util.module_from_spec(spec); spec.loader.exec_module(adtadd)
        (temp / 'in.adt').write_bytes(adt); adtadd.main(str(temp / 'in.adt'), str(temp / 'out.adt')); adt = (temp / 'out.adt').read_bytes()
    files = [('unixnu.toml', config), ('Payloads/kernel.macho', KERNEL.read_bytes()),
             ('Payloads/kernel.macho.adt', adt), ('Payloads/kernel.macho.trustcache', trust)]
    if args.gpu_addon:
        files.append(('Payloads/kernel.macho.gpuaddon', args.gpu_addon.read_bytes()))
    if seed is not None:
        files.append(('Payloads/scratch-data.img', seed))
    for name, contents in files:
        local = temp / Path(name).name
        local.write_bytes(contents)
        path = '::/EFI/UniXNU/' + name
        subprocess.run(['mcopy', '-i', str(fat), str(local), path], check=True)
        assert get(fat, path) == contents

    shim_source = Path('/home/bluekyeet/amethyst-work/donors/prev-artifacts-stage1/diag/uefi/BootShim/BootShim.S')

    def shim(payload):
        if args.shim_from:
            donor = args.shim_from.read_bytes()
            dz = zlib.decompressobj(31)
            donor_raw = dz.decompress(donor[4096:4096 + struct.unpack_from('<I', donor, 8)[0]])
            return donor_raw[:192]
        obj, binary = temp / 'shim.o', temp / 'shim.bin'
        subprocess.run(['clang', '--target=aarch64-none-elf', '-c', '-DFD_BASE=0xa7100000',
                        '-DFD_SIZE=0x300000', f'-DEMBEDDED_PAYLOAD_SIZE={payload}',
                        '-DREQUIRES_KERNEL_HEADER=1', str(shim_source), '-o', str(obj)], check=True)
        subprocess.run(['llvm-objcopy', '-O', 'binary', str(obj), str(binary)], check=True)
        return binary.read_bytes()

    payload = fat.read_bytes()
    boot = shim(len(payload))
    firmware = raw[192:prefix]
    if args.uefi_fd:
        firmware = args.uefi_fd.read_bytes()
        assert len(firmware) == 0x300000 and firmware[40:44] == b'_FVH', 'not a 3 MiB firmware volume'
    new_raw = boot + firmware + payload
    assert len(boot) == 192 and struct.unpack_from('<Q', new_raw, 16)[0] == len(new_raw)
    w = struct.unpack_from('<I', boot, 0x70)[0]
    assert ((w >> 5) & 0xffff) << (16 * ((w >> 21) & 3)) == len(payload)
    compressed = gzip.compress(new_raw, mtime=0) + d.unused_data
    header = bytearray(b[:4096])
    struct.pack_into('<I', header, 8, len(compressed))
    suffix = b[4096 + ((size + 4095) // 4096) * 4096:]
    result = bytes(header) + compressed + bytes(-len(compressed) % 4096) + suffix
    test = zlib.decompressobj(31)
    assert test.decompress(result[4096:4096 + len(compressed)]) + test.flush() == new_raw
    assert test.unused_data == d.unused_data
    DEST.write_bytes(result)
    digest = hashlib.sha256(result).hexdigest()
    (OUT / 'SHA256SUMS').write_text(f'{digest}  {DEST.name}\n')
    (OUT / 'BUILD.txt').write_text(
        f'source={SOURCE.name}\n'
        f'source_sha256={hashlib.sha256(b).hexdigest()}\n'
        f'kernel={KERNEL.name}\n'
        f'kernel_sha256={hashlib.sha256(KERNEL.read_bytes()).hexdigest()}\n'
        f'boot_args_log=sheng-log=1\n'
        f'display_probe={args.display_probe}\n'
        f'display_pm={args.display_pm}\n'
        f'display_unit_scale={args.display_unit_scale}\n'
        f'display_validate={args.display_validate}\n'
        f'display_cpu_present={args.display_cpu_present}\n'
        f'touch_snapshot={args.touch_snapshot}\n'
        f'touch_clock_vote={args.touch_clock_vote}\n'
        f'touch_dma_preflight={args.touch_dma_preflight}\n'
        f'touch_loopback={args.touch_loopback}\n'
        f'touch_goodix={args.touch_goodix}\n'
        f'touch_events={args.touch_events}\n'
        f'touch_frames={args.touch_frames}\n'
        f'touch_hid={args.touch_hid}\n'
        f'uefi_fd={args.uefi_fd}\n'
        f'gpu_addon={args.gpu_addon} owner_trust={args.owner_trust}\n'
        f'smp={args.smp}\n'
        f'msgbuf={args.msgbuf}\n'
        f'gpu_power={args.gpu_power}\n'
        f'cpufreq={args.cpufreq} cap={args.cpufreq_cap}\n'
        f'quiet_kprintf={args.quiet_kprintf}\n'
        f'data_seed_override={args.data_seed or "none"}\n'
        f'data_seed_sha256={hashlib.sha256(seed).hexdigest() if seed is not None else "none"}\n'
        f'changed=Payloads/kernel.macho + unixnu.toml logging/display settings\n'
        f'log_window=unixnu partition tail ({8192 * 4096} bytes)\n')
    print('PASS: kernel and requested logging/display settings packed; retained payloads verified')
    print('PASS: shim size handoff and ARM64 header match actual payload')
    print('Boot bytes:', len(result))
    print(digest, DEST)
