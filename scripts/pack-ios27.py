#!/usr/bin/env python3
"""Pack an iOS 27 payload into a RAM-only amethyst boot image (fastboot boot; never flash).

The container is a tested iPadOS 17 image (scripts/log/pack-log.py output): its Android boot header, its 192-byte boot shim,
whose embedded size handoff is the FAT volume's size, and that FAT volume's geometry are kept. The volume's EFI/UniXNU tree is
replaced by the one in the payload volume `unixnu build payload` writes (unixnu.toml, Payloads/kernel.macho, .adt, .trustcache,
optionally the Data seed), so the shim stays valid without rebuilding it. The firmware half is --uefi-fd (the booted firmware
with GMU_Pool reserved), as for the iPadOS images; DarwinBoot is pinned at the same revision for both OS profiles.

Usage: pack-ios27.py --container <ipados image> --payload <payload.img> --uefi-fd <fd> --out <dir> [--args "extra boot args"]
"""
import argparse
import gzip
import hashlib
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--container', type=Path, required=True)
parser.add_argument('--payload', type=Path, required=True)
parser.add_argument('--uefi-fd', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--args', default='', help='appended to the xnu entry args (letters, digits, -_.=,/ only)')
args = parser.parse_args()

image = args.container.read_bytes()
size = struct.unpack_from('<I', image, 8)[0]
d = zlib.decompressobj(31)
raw = d.decompress(image[4096:4096 + size]) + d.flush()
assert d.eof
shim, prefix = raw[:192], 192 + 0x300000
firmware = args.uefi_fd.read_bytes()
assert len(firmware) == 0x300000 and firmware[40:44] == b'_FVH', 'not a 3 MiB firmware volume'
assert b'GMU_Pool' in firmware[:0xe000], 'the firmware does not reserve GMU_Pool'


def mtools(*command, capture=False):
    return subprocess.run(list(command), check=True, capture_output=capture).stdout


with tempfile.TemporaryDirectory() as tempdir:
    temp = Path(tempdir)
    fat = temp / 'fat.img'
    fat.write_bytes(raw[prefix:])
    source = args.payload
    listing = mtools('mdir', '-/', '-b', '-i', str(source), '::/EFI/UniXNU', capture=True).decode().split()
    files = [p for p in listing if not p.endswith('/')]
    assert any(p.endswith('/Payloads/kernel.macho') for p in files), f'no kernel in the payload: {files}'
    mtools('mdeltree', '-i', str(fat), '::/EFI/UniXNU')
    mtools('mmd', '-i', str(fat), '::/EFI/UniXNU', '::/EFI/UniXNU/Payloads')
    for path in files:
        local = temp / Path(path).name
        mtools('mcopy', '-n', '-i', str(source), path, str(local))
        assert local.read_bytes() == mtools('mtype', '-i', str(source), path, capture=True), f'{path}: short copy (is TMPDIR full?)'
        if path.endswith('/unixnu.toml') and args.args:
            config = local.read_text()
            assert config.count('type = "xnu"') == 1 and '\nargs = "' in config, 'one xnu entry with args expected'
            head, _, tail = config.partition('\nargs = "')
            local.write_text(head + '\nargs = "' + tail.replace('"', ' ' + args.args + '"', 1))
        mtools('mcopy', '-i', str(fat), str(local), path)
        assert mtools('mtype', '-i', str(fat), path, capture=True) == local.read_bytes(), path
    config = mtools('mtype', '-i', str(fat), '::/EFI/UniXNU/unixnu.toml', capture=True).decode()
    payload = fat.read_bytes()

assert len(payload) == len(raw) - prefix, 'the FAT volume changed size'
new_raw = shim + firmware + payload
assert struct.unpack_from('<Q', new_raw, 16)[0] == len(new_raw), 'shim size handoff does not match'
w = struct.unpack_from('<I', shim, 0x70)[0]
assert ((w >> 5) & 0xffff) << (16 * ((w >> 21) & 3)) == len(payload), 'shim payload size does not match'
compressed = gzip.compress(new_raw, mtime=0) + d.unused_data
header = bytearray(image[:4096])
struct.pack_into('<I', header, 8, len(compressed))
suffix = image[4096 + ((size + 4095) // 4096) * 4096:]
result = bytes(header) + compressed + bytes(-len(compressed) % 4096) + suffix
check = zlib.decompressobj(31)
assert check.decompress(result[4096:4096 + len(compressed)]) + check.flush() == new_raw

args.out.mkdir(parents=True, exist_ok=True)
dest = args.out / ('Mu-' + args.out.name + '.img')
dest.write_bytes(result)
digest = hashlib.sha256(result).hexdigest()
(args.out / 'SHA256SUMS').write_text(f'{digest}  {dest.name}\n')
(args.out / 'BUILD.txt').write_text(
    f'container={args.container.name} sha256={hashlib.sha256(image).hexdigest()}\n'
    f'payload={args.payload} sha256={hashlib.sha256(args.payload.read_bytes()).hexdigest()}\n'
    f'uefi_fd={args.uefi_fd.name} sha256={hashlib.sha256(firmware).hexdigest()}\n'
    f'files={" ".join(files)}\n--- unixnu.toml\n{config}')
print(config)
print(digest, dest)
