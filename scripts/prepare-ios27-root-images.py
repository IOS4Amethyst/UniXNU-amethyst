#!/usr/bin/env python3
"""Prepare hash-pinned raw iPhone root images on macOS for policy signing."""
import argparse
import hashlib
import platform
import plistlib
from pathlib import Path
import subprocess
import sys
import tomllib
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('AEA decryption and read-only image attachment require macOS')
    core = args.core.resolve()
    profile = tomllib.loads((core / 'profiles/os/ios-27.0.1-24A446/profile.toml').read_text())
    if (profile['ipsw']['product'], profile['ipsw']['build']) != ('iPhone15,4', '24A446'):
        parser.error('Require the iPhone15,4 24A446 profile')
    output = args.output.resolve()
    if output.exists():
        parser.error('Output already exists; choose a new directory')
    output.mkdir(parents=True)
    sys.path.insert(0, str(core / 'scripts'))
    from unixnu.ipsw import Ipsw
    source = Ipsw(SimpleNamespace(board=profile, out_root=output / 'cache'), spec=profile['ipsw']['url'])
    for name, component in (('OS', 'OS'), ('SystemOS', 'Cryptex1,SystemOS')):
        dmg = source.disk_image(component)
        attached = plistlib.loads(subprocess.check_output([
            'hdiutil', 'attach', '-plist', '-nomount', '-readonly', '-noverify', '-noautofsck', str(dmg)]))
        disks = [e['dev-entry'] for e in attached['system-entities']
                 if e.get('content-hint') == 'GUID_partition_scheme']
        if len(disks) != 1:
            # Detach only devices returned by our own image attachment.
            for entry in attached['system-entities']:
                if entry.get('dev-entry'):
                    subprocess.run(['hdiutil', 'detach', entry['dev-entry']], check=False)
            raise SystemExit(f'{name}: expected one whole virtual disk')
        disk = disks[0]
        partial = output / f'{name}.raw.partial'
        try:
            info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', disk]))
            if info.get('BusProtocol') != 'Disk Image' or info.get('Writable') is not False:
                raise SystemExit(f'{name}: expected read-only Disk Image media')
            size = info['TotalSize']
            if not isinstance(size, int) or size <= 0:
                raise SystemExit(f'{name}: invalid media size')
            digest = hashlib.sha256()
            with open('/dev/r' + Path(disk).name, 'rb') as stream, partial.open('xb') as target:
                remaining = size
                while remaining:
                    block = stream.read(min(16 << 20, remaining))
                    if not block:
                        raise SystemExit(f'{name}: short raw image read')
                    target.write(block)
                    digest.update(block)
                    remaining -= len(block)
            if digest.hexdigest() != profile['root'][name.lower() + '_image_sha256']:
                raise SystemExit(f'{name}: raw image SHA-256 mismatch: {digest.hexdigest()}')
            partial.rename(output / f'{name}.raw')
            print(f'PASS: {name}: {size} bytes, SHA-256 {digest.hexdigest()}', flush=True)
        finally:
            subprocess.run(['hdiutil', 'detach', disk], check=True)
        # Only remove inputs created inside this invocation's new cache directory.
        dmg.unlink()
        archive = source.cache / Path(source.member(component)).name
        if archive != dmg:
            archive.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
