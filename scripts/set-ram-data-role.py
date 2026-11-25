#!/usr/bin/env python3
"""Use macOS diskutil to assign Data role to a packed RAM seed, preserving UUID."""
import argparse
import json
import pathlib
import plistlib
import struct
import subprocess
import sys
import tempfile
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('core', type=pathlib.Path)
parser.add_argument('source', type=pathlib.Path)
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--uuid', required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.core.resolve() / 'drivers/DarwinBoot/Test'))
import packed_ramdisk

expected = str(uuid.UUID(args.uuid)).upper()
if args.output.exists():
    parser.error('refuse to replace an existing seed')
logical, extents = packed_ramdisk.regions(args.source.read_bytes())
args.output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
    work = pathlib.Path(directory).resolve()
    raw, mount = work / 'data.raw', work / 'mount'
    with raw.open('wb') as stream:
        stream.truncate(logical)
        for offset, blob in extents:
            stream.seek(offset)
            stream.write(blob)
    mount.mkdir()
    attached = plistlib.loads(subprocess.check_output([
        'hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
        '-plist', '-nobrowse', '-mountpoint', str(mount), str(raw)]))
    volumes = [v for v in attached['system-entities'] if v.get('mount-point') == str(mount)]
    assert len(volumes) == 1
    device = volumes[0]['dev-entry']
    try:
        info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', device]))
        assert str(uuid.UUID(info['VolumeUUID'])).upper() == expected
        subprocess.run(['diskutil', 'apfs', 'changeVolumeRole', device, 'D'], check=True)
        # RamBlock's >2 GiB seed path requires persistent backing and refuses
        # volatile writes. Keep this installation in its supported RAM-only path.
        container = info['APFSContainerReference']
        subprocess.run(['diskutil', 'apfs', 'resizeContainer', container, str(2 * 1024**3)], check=True)
        subprocess.run(['sync'], check=True)
    finally:
        subprocess.run(['hdiutil', 'detach', device], check=True)
    with raw.open('r+b') as stream:
        header = stream.read(4096)
        assert header[32:36] == b'NXSB'
        block_size, blocks = struct.unpack_from('<IQ', header, 36)
        assert block_size == 4096 and blocks * block_size == 2 * 1024**3
        stream.truncate(blocks * block_size)
    logical = 2 * 1024**3
    packed_ramdisk.pack(raw, args.output)
report, _ = packed_ramdisk.inspect(args.output)
_, extents = packed_ramdisk.regions(args.output.read_bytes())
superblocks = []
for offset, blob in extents:
    for at in range(0, len(blob), 4096):
        page = blob[at:at + 4096]
        if bytes(page[32:36]) == b'APSB' and str(uuid.UUID(bytes=bytes(page[0xf0:0x100]))).upper() == expected:
            # apfs_next_doc_id is at 0x3c0; the following uint16_t apfs_role is 0x3c4.
            superblocks.append((struct.unpack_from('<Q', page, 16)[0], struct.unpack_from('<H', page, 0x3c4)[0]))
assert superblocks and max(superblocks)[1] == 0x40, superblocks
assert report['bytes'] == logical
report.update(volume_uuid=expected, apfs_role='Data', ram_only=True, hardware_tested=False,
              role_source='diskutil apfs changeVolumeRole', os_build='24A446', product='iPhone17,2')
args.output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
