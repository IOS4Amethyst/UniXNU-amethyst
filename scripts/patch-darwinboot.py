#!/usr/bin/env python3
"""Replace the DarwinBoot driver inside amethyst's booted Mu-Silicium firmware volume, in place.

The booted firmware (and .scratch/uefi-gmu/amethyst-gmu-pool.fd made from it by patch-gmu-pool.py) matches no source build,
and its DarwinBoot predates UniXNU/DarwinBoot 3c75734: it refuses arm64e kernels and kernel collections (MH_FILESET), so the
iOS 27 payload returned EFI_UNSUPPORTED. This swaps only that driver for a current DarwinBoot.efi (a `unixnu build firmware`
output) and leaves every other file of the firmware as it was.

DarwinBoot (FFS 1B64AF87-8415-4B57-9B16-680D70913B9F, a DRIVER without alignment) lives in the inner FV of the LZMA-compressed
FVMAIN file. Its PE32 section is replaced and its UI section kept; the inner FV's files are laid out again at 8-byte steps
(none after it asks for alignment, which is checked), the FV grows by whole 4 KiB blocks when the free space runs out, and
the FV image section, the compressed section and the FVMAIN file are rebuilt as patch-gmu-pool.py does (data checksums off).

Usage: patch-darwinboot.py <in.fd> <DarwinBoot.efi> <out.fd>
"""
import hashlib
import lzma
import struct
import sys
import uuid

LZMA_GUID = uuid.UUID('EE4E5898-3914-4259-9D6E-DC7BD79403CF').bytes_le
DARWINBOOT = uuid.UUID('1B64AF87-8415-4B57-9B16-680D70913B9F').bytes_le


def header_checksum(header):
    h = bytearray(header)
    h[16] = h[17] = 0
    h[23] = 0                                     # IntegrityCheck and State excluded
    return (-sum(h)) & 0xff


def fv_files(data, fv, erase):
    """(offset, bytes) of each FFS file of the FV at `fv` in `data`."""
    length = struct.unpack_from('<Q', data, fv + 32)[0]
    p = fv + struct.unpack_from('<H', data, fv + 48)[0]
    files = []
    while p < fv + length - 24:
        p = (p + 7) & ~7
        if data[p:p + 24] == bytes([erase]) * 24:
            break
        attributes = data[p + 19]
        assert not attributes & 1, 'large FFS files are not handled'
        size = int.from_bytes(data[p + 20:p + 23], 'little')
        files.append((p, bytes(data[p:p + size])))
        p += size
    assert all(b == erase for b in data[p:fv + length]), 'data after the last file'
    return files


def new_driver(old, pe):
    """The DarwinBoot FFS file with `pe` as its PE32 section and the old file's other sections."""
    sections, s = [], 24
    while s < len(old):
        s = (s + 3) & ~3
        size = int.from_bytes(old[s:s + 3], 'little')
        sections.append(old[s:s + size])
        s += size
    assert [x[3] for x in sections] == [0x10, 0x15], f'unexpected sections {[hex(x[3]) for x in sections]}'
    assert pe[:2] == b'MZ', 'not a PE image'
    body = bytearray()
    for section in [(len(pe) + 4).to_bytes(3, 'little') + b'\x10' + pe, sections[1]]:
        body += bytes(-len(body) % 4) + section
    assert len(pe) + 4 < 1 << 24
    header = bytearray(old[:24])
    header[20:23] = (24 + len(body)).to_bytes(3, 'little')
    header[17] = 0xaa                             # FFS_FIXED_CHECKSUM: file data checksum off (attribute 0x40 clear)
    header[16] = header_checksum(header)
    return bytes(header) + bytes(body)


def patch_inner(raw, pe):
    # raw: a RAW section (4 bytes), then the FV image section holding the inner FV.
    assert raw[7] == 0x17 and int.from_bytes(raw[4:7], 'little') == len(raw) - 4, 'unexpected FVMAIN layout'
    fv = 8
    assert raw[fv + 40:fv + 44] == b'_FVH'
    attributes = struct.unpack_from('<I', raw, fv + 44)[0]
    erase = 0xff if attributes & 0x800 else 0x00
    hlen = struct.unpack_from('<H', raw, fv + 48)[0]
    length = struct.unpack_from('<Q', raw, fv + 32)[0]
    blocks, block = struct.unpack_from('<II', raw, fv + 56)
    assert blocks * block == length and struct.unpack_from('<II', raw, fv + 64) == (0, 0), 'one block map entry expected'
    files = fv_files(raw, fv, erase)
    index = [i for i, (_, f) in enumerate(files) if f[:16] == DARWINBOOT]
    assert len(index) == 1, 'DarwinBoot not found exactly once'
    index = index[0]
    old = files[index][1]
    assert old[18] == 7 and old[19] == 0, 'DarwinBoot is no longer an unaligned DRIVER'
    for _, f in files[index + 1:]:
        assert not (f[19] >> 3) & 7, 'a file after DarwinBoot asks for alignment'
    files[index] = (files[index][0], new_driver(old, pe))
    inner = bytearray(raw[fv:fv + hlen])
    for offset, f in files[:index]:
        assert fv + len(inner) <= offset
        inner += bytes([erase]) * (offset - fv - len(inner)) + f      # files before DarwinBoot keep their offsets
    for _, f in files[index:]:
        inner += bytes([erase]) * (-len(inner) % 8) + f
    used = len(inner)
    while length < used:
        length += block
    inner += bytes([erase]) * (length - used)
    struct.pack_into('<Q', inner, 32, length)
    struct.pack_into('<I', inner, 56, length // block)
    struct.pack_into('<H', inner, 50, 0)
    struct.pack_into('<H', inner, 50, (-sum(struct.unpack_from(f'<{hlen // 2}H', inner))) & 0xffff)
    assert sum(struct.unpack_from(f'<{hlen // 2}H', inner)) & 0xffff == 0
    section = (len(inner) + 4).to_bytes(3, 'little') + b'\x17'
    assert len(inner) + 4 < 1 << 24
    return raw[:4] + section + bytes(inner), len(old), len(files[index][1]), length


def main(src, efi, dst):
    fd = bytearray(open(src, 'rb').read())
    pe = open(efi, 'rb').read()
    assert len(fd) == 0x300000 and fd[40:44] == b'_FVH'
    fvlen = struct.unpack_from('<Q', fd, 32)[0]
    attributes = struct.unpack_from('<I', fd, 44)[0]
    erase = 0xff if attributes & 0x800 else 0x00
    files = fv_files(fd, 0, erase)
    fv_at, fv_file = files[1]
    fv_size = len(fv_file)
    assert fd[fv_at + 18] == 11 and not (fd[fv_at + 19] & 0x40)
    s = fv_at + 24
    assert fd[s + 3] == 0x02 and fd[s + 4:s + 20] == LZMA_GUID and int.from_bytes(fd[s:s + 3], 'little') == fv_size - 24
    data_off, guided = struct.unpack_from('<HH', fd, s + 20)
    assert not guided & 2, 'guided section carries an auth status/CRC'
    comp = bytes(fd[s + data_off:s + fv_size - 24])
    props, dict_size, raw_size = comp[0], struct.unpack_from('<I', comp, 1)[0], struct.unpack_from('<Q', comp, 5)[0]
    filters = [{'id': lzma.FILTER_LZMA1, 'dict_size': dict_size, 'lc': props % 9, 'lp': (props // 9) % 5, 'pb': props // 45}]
    raw = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters).decompress(comp[13:])
    assert len(raw) == raw_size
    raw2, old_size, new_size, inner_length = patch_inner(raw, pe)
    body = lzma.compress(raw2, format=lzma.FORMAT_RAW, filters=[dict(filters[0], preset=9 | lzma.PRESET_EXTREME)])
    assert lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters).decompress(body)[:len(raw2)] == raw2
    comp2 = comp[:5] + struct.pack('<Q', len(raw2)) + body
    new_section = bytearray(fd[s:s + data_off]) + comp2
    new_section[0:3] = len(new_section).to_bytes(3, 'little')
    new_file = bytearray(fd[fv_at:fv_at + 24]) + new_section
    new_file[20:23] = len(new_file).to_bytes(3, 'little')
    new_file[16] = header_checksum(new_file[:24])
    assert all(b == erase for b in fd[fv_at + fv_size:fvlen]), 'something follows FVMAIN'
    assert fv_at + len(new_file) <= fvlen, 'the firmware volume is full'
    fd[fv_at:fvlen] = new_file + bytes([erase]) * (fvlen - fv_at - len(new_file))
    open(dst, 'wb').write(fd)
    print(f'DarwinBoot file {old_size:#x} -> {new_size:#x} bytes (PE {len(pe):#x}); inner FV {inner_length:#x}; '
          f'FVMAIN {fv_size:#x} -> {len(new_file):#x}; sha256 {hashlib.sha256(fd).hexdigest()}')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
