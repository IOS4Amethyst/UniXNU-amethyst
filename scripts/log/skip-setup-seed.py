#!/usr/bin/env python3
"""macOS only: copy a packed RAM Data seed with Setup marked done, so SpringBoard
starts instead of the "Press home to upgrade" Setup page.

Usage: skip-setup-seed.py INPUT OUTPUT --packer /path/to/packed_ramdisk.py
       skip-setup-seed.py --check

Preference values are those of UniXNU scripts/seed-data-skip-setup.py
(commit 12c5d44, with --no-auto-dim), which the sheng port booted on hardware.
Differences: the volume is attached with ownership honored, written files are
owned by mobile (501:501) like the rest of /private/var/mobile, symlinks are
refused, and the volume UUID is checked so the kernel configuration still
matches. Never edits the input or opens a physical disk.
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

BUILD = '21H461'
MOBILE = 501
VOLUME_UUID = 'A173DCC8-1F31-46ED-9F67-DB2A68FC25DE'
PREFERENCES = {
    'com.apple.migration': {
        'LastSystemVersion': BUILD,
        'DMUserDataDisposition': {'SyntheticDidUpgrade': False, 'buildVersion': BUILD}},
    'com.apple.springboard': {
        'BKNoWatchdogs': True,
        'FBLaunchWatchdogExceptions': {'com.apple.purplebuddy': 100.0},
        'MCForceShowCloudConfigurationUI': False,
        # No touch yet: keep the display from dimming and the system from sleeping.
        'SBDisableAutoDim': True, 'SBDontDimOrLockOnAC': True, 'SBDisableIdleSleep': True},
    'com.apple.purplebuddy': {
        'SetupDone': True, 'SetupFinishedAllSteps': True, 'SetupVersion': 11,
        'daemonizeNeedsToRun': False},
    'com.apple.purplebuddy.notbackedup': {k: False for k in (
        'ShouldRunDiagnosticsMiniBuddy', 'ShouldRunHSA2MiniBuddy',
        'ShouldRunKeychainSyncMiniBuddy', 'ShouldRunMesaMiniBuddy',
        'ShouldRunPaymentMiniBuddy', 'ShouldRunStandardMiniBuddy', 'SilentICDPUpgrade')},
    'com.apple.managedconfiguration.notbackedup': {
        'LockdownActivationIndicatesCloudConfigurationAvailable': False},
}
# launchd overrides (system domain). The first four are UniXNU skip-setup.md's
# list for 21H461 ("services that block the first boot"); the kernel lacks
# sheng-disable-jobs, so they go in disabled.plist like the watchdog override.
# bluetoothd and callservicesd only respawn every 10s here and flood msgbuf.
DISABLED_JOBS = ('com.apple.identityservicesd', 'com.apple.locationd', 'com.apple.CommCenter',
                 'com.apple.iconservices.iconservicesagent',
                 'com.apple.bluetoothd', 'com.apple.telephonyutilities.callservicesd',
                 # Run 2 (v2): these crash and respawn every 10s; their spam pushes the first
                 # 220s (SpringBoard start) out of the 128 KiB log window. Unknown labels are ignored.
                 'com.apple.fitcored', 'com.apple.liveactivitiesd', 'com.apple.privacyaccountingd',
                 'com.apple.ScreenTimeAgent', 'com.apple.dmd', 'com.apple.assistantd',
                 'com.apple.biomesyncd', 'com.apple.biomed')
# GPU compositing (amethyst v48/v49): with the Metal plugin published, backboardd allocated Apple-compressed
# framebuffers ('b3a8', two planes), which the GOP display path cannot scan out, so every swap failed verify_swap
# and the screen stayed black. UniXNU add-gpu-plugin-data.py --publish and make-data-seed.py set these three for
# backboardd and the global domain, for mobile and root; the render server reads them at launch.
CA_FRAMEBUFFER = {'CAEnableDeepFramebuffer': False, 'CAEnableFramebufferCompression': False,
                  'CAEnableIOSurfaceCompression': False}
for _domain in ('backboardd', '.GlobalPreferences'):
    PREFERENCES[_domain] = dict(CA_FRAMEBUFFER)
CONTAINERS = ('Bundle', 'Data/System', 'Shared/SystemGroup', 'Temp')


def merged(previous, values):
    if not isinstance(previous, dict):
        raise ValueError('preference file must be a dictionary')
    return {**previous, **values}


def disable_jobs(var):
    policy = var / 'db/com.apple.xpc.launchd/disabled.plist'
    marker = policy.with_name('disabled.migrated')
    for path in (policy, marker):
        if path.is_symlink():
            raise ValueError(f'refusing symlink {path}')
    previous = plistlib.loads(policy.read_bytes()) if policy.exists() else {}
    data = merged(previous, {job: True for job in DISABLED_JOBS})
    policy.parent.mkdir(parents=True, exist_ok=True)
    staged = policy.with_suffix('.plist.new')
    with staged.open('xb') as stream:
        plistlib.dump(data, stream)
    os.chown(staged, 0, 0)
    staged.chmod(0o644)
    staged.replace(policy)
    if not marker.exists():
        marker.touch()
    os.chown(marker, 0, 0)
    marker.chmod(0o644)
    assert plistlib.loads(policy.read_bytes()) == data
    return sorted(k for k, v in data.items() if v)


def write_preferences(var):
    prefs = var / 'mobile/Library/Preferences'
    for part in (var / 'mobile', var / 'mobile/Library', prefs):
        if part.is_symlink():
            raise ValueError(f'refusing symlink {part}')
    if not (var / 'mobile').is_dir():
        raise ValueError('seed has no /mobile; wrong base image')
    if not prefs.exists():
        prefs.mkdir(parents=True)
        for part in (var / 'mobile/Library', prefs):
            os.chown(part, MOBILE, MOBILE)
            part.chmod(0o755)
    written = {}
    for name, values in PREFERENCES.items():
        path = prefs / (name + '.plist')
        if path.is_symlink():
            raise ValueError(f'refusing symlink {path}')
        previous = plistlib.loads(path.read_bytes()) if path.exists() else {}
        data = merged(previous, values)
        staged = path.with_suffix('.plist.new')
        with staged.open('xb') as stream:
            plistlib.dump(data, stream, fmt=plistlib.FMT_BINARY)
        os.chown(staged, MOBILE, MOBILE)
        staged.chmod(0o600)
        staged.replace(path)
        assert plistlib.loads(path.read_bytes()) == data
        assert path.stat().st_uid == MOBILE and path.stat().st_gid == MOBILE
        written[name] = sorted(values)
    root_prefs = var / 'root/Library/Preferences'
    if (var / 'root').is_dir() and not (var / 'root').is_symlink():
        for part in (var / 'root/Library', root_prefs):
            if part.is_symlink():
                raise ValueError(f'refusing symlink {part}')
        root_prefs.mkdir(parents=True, exist_ok=True)
        for domain in ('backboardd', '.GlobalPreferences'):
            path = root_prefs / (domain + '.plist')
            if path.is_symlink():
                raise ValueError(f'refusing symlink {path}')
            previous = plistlib.loads(path.read_bytes()) if path.exists() else {}
            data = merged(previous, CA_FRAMEBUFFER)
            staged = path.with_suffix('.plist.new')
            with staged.open('xb') as stream:
                plistlib.dump(data, stream, fmt=plistlib.FMT_BINARY)
            os.chown(staged, 0, 0)
            staged.chmod(0o600)
            staged.replace(path)
            written['root:' + domain] = sorted(CA_FRAMEBUFFER)
    created = []
    for sub in CONTAINERS:
        d = var / 'containers' / sub
        if d.is_symlink():
            raise ValueError(f'refusing symlink {d}')
        if not d.is_dir():
            d.mkdir(parents=True)
            d.chmod(0o755)
            created.append(sub)
    return written, created


def check():
    original = {'Other': 1, 'SetupDone': False}
    out = merged(original, PREFERENCES['com.apple.purplebuddy'])
    assert out['Other'] == 1 and out['SetupDone'] is True and out['SetupVersion'] == 11
    assert original['SetupDone'] is False
    try:
        merged([], {})
    except ValueError:
        pass
    else:
        raise AssertionError('accepted non-dictionary')
    with tempfile.TemporaryDirectory() as td:
        var = Path(td)
        (var / 'mobile').mkdir()
        if os.geteuid() == 0:
            written, created = write_preferences(var)
            assert set(written) == set(PREFERENCES) and created == list(CONTAINERS)
            assert plistlib.loads((var / 'mobile/Library/Preferences/backboardd.plist').read_bytes())['CAEnableFramebufferCompression'] is False
            again = plistlib.loads((var / 'mobile/Library/Preferences/com.apple.purplebuddy.plist').read_bytes())
            assert again['SetupFinishedAllSteps'] is True
            (var / 'db/com.apple.xpc.launchd').mkdir(parents=True)
            (var / 'db/com.apple.xpc.launchd/disabled.plist').write_bytes(
                plistlib.dumps({'com.apple.watchdogd': True, 'other': False}))
            jobs = disable_jobs(var)
            assert 'com.apple.watchdogd' in jobs and 'com.apple.locationd' in jobs and 'other' not in jobs
    print('PASS: Setup preferences merged; existing keys preserved')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path, nargs='?')
    parser.add_argument('output', type=Path, nargs='?')
    parser.add_argument('--packer', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        return check()
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
    work = Path(tempfile.mkdtemp(prefix='skip-setup-seed-', dir=args.output.resolve().parent))
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
        if info['VolumeUUID'] != VOLUME_UUID:
            raise ValueError('unexpected seed volume UUID; kernel configuration must not change')
        written, created = write_preferences(mount)
        disabled = disable_jobs(mount)
        after = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(mount)]))
        assert after['VolumeUUID'] == VOLUME_UUID
    finally:
        subprocess.run(['hdiutil', 'detach', str(mount)], check=True)
    if raw.read_bytes()[32:36] != b'NXSB':
        raise RuntimeError('edited image is no longer an APFS container')
    subprocess.run([sys.executable, str(args.packer.resolve()), str(raw),
                    str(args.output.resolve())], check=True)
    args.output.with_suffix(args.output.suffix + '.json').write_text(json.dumps({
        'input': str(args.input.resolve()), 'volume_uuid': VOLUME_UUID,
        'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'ram_only': True, 'hardware_tested': False, 'build': BUILD,
        'preferences': written, 'containers_created': created, 'disabled_jobs': disabled,
        'source': 'UniXNU scripts/seed-data-skip-setup.py 12c5d44 --no-auto-dim',
        'work_directory': str(work),
    }, indent=2) + '\n')
    print('Created Setup-skipped RAM seed:', args.output)


if __name__ == '__main__':
    main()
