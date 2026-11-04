#!/usr/bin/env python3
"""Root pins of the iPhone15,4 iOS 27.0.1 (24A446) profile, read on macOS.

The [root] table of profiles/os/ios-27.0.1-24A446 needs, for the OS and the
Cryptex1,SystemOS disk images: the APFS volume UUIDs and the SHA-256 of the raw
disk (the bytes UniXNU's userdata step writes into the window and hashes).
Both images ship AEA-encrypted, so this runs where /usr/bin/aea exists.

Only the two members are fetched, with HTTP ranges (the IPSW stores them
uncompressed); each is decrypted, measured and deleted before the next one so
the runner's disk holds at most one image pair. Nothing is uploaded except the
JSON report.
"""
import hashlib
import json
import plistlib
import re
import struct
import subprocess
import sys
import urllib.request
import zlib
from pathlib import Path

URL = ('https://updates.cdn-apple.com/2026FallFCS/8797a573-d5d1-47eb-9231-d3af255806bf/'
       'iPhone15,4_27.0.1_24A446_Restore.ipsw')
CHUNK = 1 << 24


def get(url, start, end):
    request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end - 1}'})
    with urllib.request.urlopen(request) as response:
        return response.read()


def directory(url):
    """Central directory of a remote zip (ZIP64 aware): name -> (method, csize, usize, header offset)."""
    head = urllib.request.urlopen(urllib.request.Request(url, method='HEAD'))
    size = int(head.headers['Content-Length'])
    tail = get(url, size - 65536, size)
    at = tail.rfind(b'PK\x06\x06')
    if at >= 0:
        cd_size, cd_offset = struct.unpack_from('<QQ', tail, at + 40)
    else:
        at = tail.rfind(b'PK\x05\x06')
        cd_size, cd_offset = struct.unpack_from('<II', tail, at + 12)
    cd, at, entries = get(url, cd_offset, cd_offset + cd_size), 0, {}
    while cd[at:at + 4] == b'PK\x01\x02':
        method, = struct.unpack_from('<H', cd, at + 10)
        csize, usize = struct.unpack_from('<II', cd, at + 20)
        name_len, extra_len, comment_len = struct.unpack_from('<HHH', cd, at + 28)
        offset, = struct.unpack_from('<I', cd, at + 42)
        name = cd[at + 46:at + 46 + name_len].decode()
        extra, q = cd[at + 46 + name_len:at + 46 + name_len + extra_len], 0
        while q < len(extra):
            tag, length = struct.unpack_from('<HH', extra, q)
            if tag == 1:
                values = list(struct.unpack_from('<' + 'Q' * (length // 8), extra, q + 4))
                if usize == 0xffffffff: usize = values.pop(0)
                if csize == 0xffffffff: csize = values.pop(0)
                if offset == 0xffffffff: offset = values.pop(0)
            q += 4 + length
        entries[name] = (method, csize, usize, offset)
        at += 46 + name_len + extra_len + comment_len
    return entries


def fetch(url, entries, name, out):
    method, csize, usize, offset = entries[name]
    header = get(url, offset, offset + 30)
    name_len, extra_len = struct.unpack_from('<HH', header, 26)
    start = offset + 30 + name_len + extra_len
    digest = hashlib.sha256()
    inflate = zlib.decompressobj(-15) if method == 8 else None
    if method not in (0, 8):
        raise SystemExit(f'{name}: zip method {method}')
    with open(out, 'wb') as target:
        for at in range(start, start + csize, CHUNK):
            data = get(url, at, min(at + CHUNK, start + csize))
            data = inflate.decompress(data) if inflate else data
            digest.update(data)
            target.write(data)
    if out.stat().st_size != usize:
        raise SystemExit(f'{name}: {out.stat().st_size} bytes, expected {usize}')
    return digest.hexdigest()


def decrypt(archive):
    result = subprocess.run(['ipsw', 'fw', 'aea', '--key', str(archive)], capture_output=True, text=True)
    keys = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith('base64:')]
    if result.returncode or len(keys) != 1:
        raise SystemExit(f'AEA key of {archive.name}: {result.stderr.strip()[-300:]}')
    plain = archive.with_suffix('')
    subprocess.run(['/usr/bin/aea', 'decrypt', '-i', str(archive), '-o', str(plain), '-key-value', keys[0]],
                   check=True, capture_output=True)
    return plain


def measure(dmg):
    """Raw disk size and SHA-256 (as unixnu/userdata.py reads it) and the APFS volumes of the image."""
    info = plistlib.loads(subprocess.run(['hdiutil', 'imageinfo', '-plist', str(dmg)],
                                         capture_output=True, check=True).stdout)
    size = info['Size Information']['Total Bytes']
    out = subprocess.run(['hdiutil', 'attach', '-nomount', '-readonly', '-noverify', '-noautofsck', str(dmg)],
                         capture_output=True, text=True, check=True).stdout
    disk = re.search(r'(/dev/disk\d+)\s', out).group(1)
    try:
        digest, done = hashlib.sha256(), 0
        with open(disk.replace('/dev/disk', '/dev/rdisk'), 'rb', buffering=0) as raw:
            while done < size:
                data = raw.read(min(CHUNK, size - done))
                if not data:
                    break
                digest.update(data)
                done += len(data)
        if done != size:
            raise SystemExit(f'{dmg.name}: read {done} of {size} bytes')
        listing = plistlib.loads(subprocess.run(['diskutil', 'apfs', 'list', '-plist'],
                                                capture_output=True, check=True).stdout)
        base = Path(disk).name
        volumes = []
        for container in listing.get('Containers', []):
            stores = [s.get('DeviceIdentifier', '') for s in container.get('PhysicalStores', [])]
            if any(s.startswith(base + 's') or s == base for s in stores):
                for volume in container.get('Volumes', []):
                    volumes.append({'name': volume.get('Name'), 'uuid': volume.get('APFSVolumeUUID'),
                                    'roles': volume.get('Roles', [])})
        return {'raw_size': size, 'raw_sha256': digest.hexdigest(), 'volumes': volumes}
    finally:
        subprocess.run(['hdiutil', 'detach', disk, '-force'], capture_output=True)


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 else 'work')
    work.mkdir(parents=True, exist_ok=True)
    entries = directory(URL)
    report = {'ipsw': URL, 'product': 'iPhone15,4', 'build': '24A446', 'images': {}}
    for component, member in (('Cryptex1,SystemOS', '043-69467-710.dmg.aea'), ('OS', '043-70116-663.dmg.aea')):
        archive = work / member
        print(f'fetch {member}', flush=True)
        aea_sha = fetch(URL, entries, member, archive)
        print(f'decrypt {member}', flush=True)
        plain = decrypt(archive)
        archive.unlink()
        print(f'measure {plain.name}', flush=True)
        result = measure(plain)
        result.update({'member': member, 'aea_sha256': aea_sha})
        report['images'][component] = result
        plain.unlink()
        print(json.dumps(result, indent=1), flush=True)
    Path('root-pins.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
