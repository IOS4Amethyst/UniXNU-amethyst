#!/usr/bin/env python3
"""Keep the boot trust cache in step with the GPU addon (RAM-boot images only).

The D94 RELEASE kernel loads only the trust cache's bundle modules 0 and 1, so the addon's Mach-O CDHashes (NativeAdrenoMetal plugin,
Turnip) live in module 1 (b19b: 130 -> 132 entries). A rebuilt addon has new CDHashes: replace the old addon's entries with the new
addon's, keeping each entry's hash type, flags and category, and the module sorted.

  addon-trustcache.py show ADDON [TRUSTCACHE]
  addon-trustcache.py replace OLD_ADDON NEW_ADDON TRUSTCACHE OUT
  addon-trustcache.py add ADDON NAME TEXT OUT    (a plain data file, e.g. tu_debug -> /private/var/ngpu/tu_debug; no CDHash)

Addon (make-gpu-addon.py): "NGPUADD2", u32 count, u32 0, u64 total; per file u32 path bytes (with NUL), u32 mode, u64 size, u64 packed
size, the path and the zlib data, each padded to 8. Trust cache container: u32 module count, u32 offsets; module: u32 version 2,
16-byte UUID, u32 entries, entries of 20-byte CDHash, u8 hash type, u8 flags, u16 category.
"""
import hashlib
import struct
import sys
import zlib


def pad8(n):
    return (n + 7) & ~7


def addon_files(blob):
    assert blob[:8] == b'NGPUADD2', 'not an NGPUADD2 addon'
    count, _, total = struct.unpack_from('<IIQ', blob, 8)
    assert total == len(blob), (total, len(blob))
    at, files = 24, {}
    for _ in range(count):
        name_len, mode, size, packed = struct.unpack_from('<IIQQ', blob, at)
        at += 24
        name = blob[at:at + name_len - 1].decode()
        at += pad8(name_len)
        data = zlib.decompress(blob[at:at + packed])
        assert len(data) == size, name
        at += pad8(packed)
        files[name] = (mode, data)
    assert at == len(blob)
    return files


def cdhashes(macho):
    """(hash type, 20-byte CDHash) of every CodeDirectory in a thin 64-bit Mach-O's signature."""
    if macho[:4] != b'\xcf\xfa\xed\xfe':
        return []
    ncmds, = struct.unpack_from('<I', macho, 16)
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', macho, at)
        if cmd == 0x1d:   # LC_CODE_SIGNATURE
            off, length = struct.unpack_from('<II', macho, at + 8)
            sig = macho[off:off + length]
            magic, _, count = struct.unpack_from('>III', sig, 0)
            assert magic == 0xfade0cc0, hex(magic)
            out = []
            for k in range(count):
                slot, boff = struct.unpack_from('>II', sig, 12 + 8 * k)
                if slot == 0 or 0x1000 <= slot < 0x1005:
                    cmagic, clen = struct.unpack_from('>II', sig, boff)
                    assert cmagic == 0xfade0c02
                    cd = sig[boff:boff + clen]
                    hash_type = cd[37]
                    digest = {1: hashlib.sha1, 2: hashlib.sha256, 3: hashlib.sha256, 4: hashlib.sha384}[hash_type](cd).digest()
                    out.append((hash_type, digest[:20]))
            return out
        at += size
    return []


def addon_cdhashes(blob):
    out = {}
    for name, (_, data) in addon_files(blob).items():
        for hash_type, h in cdhashes(data):
            out[h] = (name, hash_type)
    return out


def modules(tc):
    count, = struct.unpack_from('<I', tc, 0)
    offs = list(struct.unpack_from('<%dI' % count, tc, 4)) + [len(tc)]
    mods = []
    for k in range(count):
        m = tc[offs[k]:offs[k + 1]]
        version, = struct.unpack_from('<I', m, 0)
        uuid = m[4:20]
        n, = struct.unpack_from('<I', m, 20)
        assert version == 2 and len(m) == 24 + 24 * n, (k, version, n, len(m))
        mods.append((uuid, [m[24 + 24 * i:48 + 24 * i] for i in range(n)]))
    return mods


def build(mods):
    blobs = [struct.pack('<I', 2) + uuid + struct.pack('<I', len(e)) + b''.join(e) for uuid, e in mods]
    head = 4 + 4 * len(blobs)
    offs, at = [], head
    for b in blobs:
        offs.append(at)
        at += len(b)
    return struct.pack('<I', len(blobs)) + struct.pack('<%dI' % len(blobs), *offs) + b''.join(blobs)


def build_addon(files):
    body = b''
    for name, (mode, data) in files.items():
        raw = name.encode() + b'\0'
        packed = zlib.compress(data, 9)
        body += struct.pack('<IIQQ', len(raw), mode, len(data), len(packed))
        body += raw + bytes(pad8(len(raw)) - len(raw)) + packed + bytes(pad8(len(packed)) - len(packed))
    return b'NGPUADD2' + struct.pack('<IIQ', len(files), 0, 24 + len(body)) + body


def main():
    if sys.argv[1] == 'add':
        blob = open(sys.argv[2], 'rb').read()
        files = addon_files(blob)
        # Before the *.publish markers, which the plugin's install treats as the last step.
        added = {sys.argv[3]: (0o644, sys.argv[4].encode() + b'\n')}
        files = {**{k: v for k, v in files.items() if not k.endswith('.publish')}, **added,
                 **{k: v for k, v in files.items() if k.endswith('.publish')}}
        out = build_addon(files)
        assert addon_files(out) == files
        open(sys.argv[5], 'wb').write(out)
        print(f'{len(files)} files, {len(out)} bytes', hashlib.sha256(out).hexdigest())
        return
    if sys.argv[1] == 'show':
        hashes = addon_cdhashes(open(sys.argv[2], 'rb').read())
        mods = modules(open(sys.argv[3], 'rb').read()) if len(sys.argv) > 3 else []
        for h, (name, t) in sorted(hashes.items(), key=lambda x: x[1]):
            where = [str(k) for k, (_, e) in enumerate(mods) if any(x[:20] == h for x in e)]
            print(h.hex(), 'type', t, name, 'modules', ','.join(where) or '-')
        return
    assert sys.argv[1] == 'replace'
    old = addon_cdhashes(open(sys.argv[2], 'rb').read())
    new = addon_cdhashes(open(sys.argv[3], 'rb').read())
    tc = open(sys.argv[4], 'rb').read()
    mods = modules(tc)
    assert build(mods) == tc, 'trust cache does not round-trip'
    uuid, entries = mods[1]
    removed = [e for e in entries if e[:20] in old]
    assert removed, 'none of the old addon CDHashes are in module 1'
    template = removed[0][20:]
    kept = [e for e in entries if e[:20] not in old]
    for k, (_, e) in enumerate(mods):
        if k != 1:
            assert not any(x[:20] in new for x in e), f'a new CDHash is already in module {k}'
    added = [h + bytes([t]) + template[1:] for h, (_, t) in new.items()]
    mods[1] = (uuid, sorted(kept + added, key=lambda e: e[:20]))
    out = build(mods)
    open(sys.argv[5], 'wb').write(out)
    print(f'module 1: {len(entries)} -> {len(mods[1][1])} entries; removed {len(removed)}, added {len(added)}')
    for e in removed:
        print('  -', e[:20].hex(), old[e[:20]][0])
    for h, (name, t) in new.items():
        print('  +', h.hex(), name)
    print(hashlib.sha256(out).hexdigest())


if __name__ == '__main__':
    main()
