#!/usr/bin/env python3
"""macOS only: create a temporary watchdog-disabled copy of a packed RAM Data seed.

Usage: disable-watchdog-seed.py INPUT OUTPUT --packer /path/to/packed_ramdisk.py
Never edits the input or opens a physical disk. Revert by using the original seed.
Remove this bring-up override when a real watchdog backend is available.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile


def disable_jobs(data):
    if not isinstance(data, dict):
        raise ValueError('launchd disabled.plist must be a dictionary')
    return {**data, 'com.apple.watchdogd': True, 'com.apple.watchdog': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, nargs='?')
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--packer', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        original = {'other.job': False, 'com.apple.watchdogd': False}
        changed = disable_jobs(plistlib.loads(plistlib.dumps(original)))
        assert changed == {'other.job': False, 'com.apple.watchdogd': True,
                           'com.apple.watchdog': True}
        assert original['com.apple.watchdogd'] is False
        try:
            disable_jobs([])
        except ValueError:
            pass
        else:
            raise AssertionError('accepted non-dictionary')
        print('PASS: only watchdog overrides changed; original preserved')
        return
    if not args.input or not args.output or not args.packer:
        parser.error('INPUT, OUTPUT and --packer required')
    if sys.platform != 'darwin':
        parser.error('APFS mutation requires macOS hdiutil; input left untouched')
    if args.output.exists() or args.input.resolve() == args.output.resolve():
        parser.error('output must be a new file')
    spec = importlib.util.spec_from_file_location('packed_ramdisk', args.packer)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    logical, extents = module.regions(args.input.read_bytes())
    # ponytail: local host image only; retain work files on failure for inspection.
    work = Path(tempfile.mkdtemp(prefix='watchdog-seed-', dir=args.output.resolve().parent))
    raw, mount = work / 'data.img', work / 'mount'
    mount.mkdir()
    with raw.open('xb') as stream:
        stream.truncate(logical)
        for offset, data in extents:
            stream.seek(offset)
            stream.write(data)
    result = subprocess.run(['hdiutil', 'attach', '-plist', '-nobrowse', '-owners', 'on',
                             '-mountpoint', str(mount), str(raw)],
                            capture_output=True, check=True)
    attached = plistlib.loads(result.stdout)
    devices = [e['dev-entry'] for e in attached.get('system-entities', []) if e.get('dev-entry')]
    mounted = [e for e in attached.get('system-entities', []) if e.get('mount-point')]
    if len(mounted) != 1 or Path(mounted[0]['mount-point']).resolve() != mount.resolve():
        if devices:
            subprocess.run(['hdiutil', 'detach', devices[0]], check=True)
        raise RuntimeError('unexpected mounts; refusing to edit')
    try:
        info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(mount)]))
        volume_uuid = info['VolumeUUID']
        if volume_uuid != 'A173DCC8-1F31-46ED-9F67-DB2A68FC25DE':
            raise ValueError('unexpected seed volume UUID; kernel configuration must not change')
        policy = mount / 'db/com.apple.xpc.launchd/disabled.plist'
        if policy.is_symlink():
            raise ValueError('refusing a symlink override file')
        previous = plistlib.loads(policy.read_bytes()) if policy.exists() else {}
        updated = disable_jobs(previous)
        policy.parent.mkdir(parents=True, exist_ok=True)
        staged = policy.with_suffix('.plist.new')
        with staged.open('xb') as stream:
            plistlib.dump(updated, stream)
        staged.chmod(0o644)
        os.chown(staged, 0, 0)
        staged.replace(policy)
        # This OS selects the modern override loader using this migration marker.
        marker = policy.with_name('disabled.migrated')
        if marker.is_symlink():
            raise ValueError('refusing a symlink migration marker')
        if not marker.exists():
            marker.touch(exist_ok=False)
        marker.chmod(0o644)
        os.chown(marker, 0, 0)
        assert plistlib.loads(policy.read_bytes()) == updated
        assert policy.stat().st_uid == 0 and marker.stat().st_uid == 0
        assert marker.is_file()
        after = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(mount)]))
        assert after['VolumeUUID'] == volume_uuid
    finally:
        subprocess.run(['hdiutil', 'detach', str(mount)], check=True)
    subprocess.run([sys.executable, str(args.packer.resolve()), str(raw),
                    str(args.output.resolve())], check=True)
    args.output.with_suffix(args.output.suffix + '.json').write_text(json.dumps({
        'input': str(args.input.resolve()), 'volume_uuid': volume_uuid,
        'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'ram_only': True, 'hardware_tested': False,
        'disabled_jobs': ['com.apple.watchdogd', 'com.apple.watchdog'],
        'migration_marker': 'db/com.apple.xpc.launchd/disabled.migrated',
        'expiry': 'remove when a real watchdog backend is available',
        'work_directory': str(work),
    }, indent=2) + '\n')
    print('Created temporary watchdog-disabled RAM seed:', args.output)


if __name__ == '__main__':
    main()
