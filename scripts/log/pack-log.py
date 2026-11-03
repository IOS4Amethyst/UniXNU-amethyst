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
parser.add_argument('--shim-from', type=Path,
                    help='reuse the 192-byte boot shim of an earlier packed image (when the donor BootShim.S is gone); '
                         'its encoded payload size and total size are still asserted')
parser.add_argument('--touch-goodix', action='store_true',
                    help='after a passing loopback: rails, reset, SPI pins, one Goodix identity read; requires --touch-loopback')
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
    files = [('unixnu.toml', config), ('Payloads/kernel.macho', KERNEL.read_bytes()),
             ('Payloads/kernel.macho.adt', adt), ('Payloads/kernel.macho.trustcache', trust)]
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
    new_raw = boot + raw[192:prefix] + payload
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
        f'data_seed_override={args.data_seed or "none"}\n'
        f'data_seed_sha256={hashlib.sha256(seed).hexdigest() if seed is not None else "none"}\n'
        f'changed=Payloads/kernel.macho + unixnu.toml logging/display settings\n'
        f'log_window=unixnu partition tail ({8192 * 4096} bytes)\n')
    print('PASS: kernel and requested logging/display settings packed; retained payloads verified')
    print('PASS: shim size handoff and ARM64 header match actual payload')
    print('Boot bytes:', len(result))
    print(digest, DEST)
