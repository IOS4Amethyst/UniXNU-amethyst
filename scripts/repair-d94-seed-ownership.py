#!/usr/bin/env python3
"""macOS root only: repair metadata in a NEW packed D94 RAM Data seed.

INPUT OUTPUT --os-image OS.raw --packer packed_ramdisk.py
Unknown paths abort before edits; OUTPUT.json records plans, edits and failures.
No file contents, launchd policy, Setup or migration preferences are written.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import stat
import subprocess
import sys
import tempfile

SEED_SHA = '2e0525e0b7d936a80ef9e8579e32780e83882e0de1228aefa37e7942d4b665a0'
OS_SHA = '4b8719fe44a2b80cba9e108ebcec91b6b8a03a3c3ffe353befce3b100f2e6f25'
UUID = '9EE3A4D1-60A0-49AD-9D49-B960370FF230'
SIZE = 2 * 1024**3


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
        return value.hexdigest()


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'not a regular non-symlink image/file: {path}')
    return path


def safe_path(root, relative):
    """Allow a final symlink (lstat/lchown only), never a symlink ancestor."""
    parts = PurePosixPath(relative).parts
    if relative != '.' and (not parts or relative.startswith('/') or
                            any(p in ('', '..', '.') for p in relative.split('/'))):
        raise ValueError(f'invalid relative path: {relative}')
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f'root is not a real directory: {root}')
    path = root
    for part in parts[:-1]:
        path /= part
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError(f'non-directory/symlink ancestor: {path}')
    return root.joinpath(*parts)


def metadata(path, *, allow_hardlinks=False):
    s = path.lstat()
    kind = stat.S_IFMT(s.st_mode)
    if kind not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
        raise ValueError(f'unsupported inode: {path}')
    if kind == stat.S_IFREG and s.st_nlink != 1 and not allow_hardlinks:
        raise ValueError(f'hardlinked file refused: {path}')
    return dict(uid=s.st_uid, gid=s.st_gid, mode=stat.S_IMODE(s.st_mode), kind=kind)


def inventory(root):
    result = {}
    def visit(relative):
        path = safe_path(root, relative)
        item = metadata(path)
        if item['kind'] == stat.S_IFLNK:
            item['target'] = os.readlink(path)
        elif item['kind'] == stat.S_IFREG:
            item['sha256'] = digest(path)
        result[relative] = item
        if item['kind'] == stat.S_IFDIR:
            for name in sorted(os.listdir(path)):
                visit(name if relative == '.' else relative + '/' + name)
    visit('.')
    return result


def inventory_changes(expected, actual):
    return [dict(path=path, expected=expected.get(path), actual=actual.get(path))
            for path in sorted(expected.keys() | actual.keys())
            if expected.get(path) != actual.get(path)]


def source_metadata(path):
    # Source is read-only: reading a shared inode cannot mutate any of its aliases.
    return metadata(path, allow_hardlinks=True)


def plan(seed, source, read_metadata=source_metadata):
    """Pure planner: source is the actual OS/private/var, not OS/var."""
    changes, unknown = [], []
    for relative, current in inventory(seed).items():
        try:
            path = safe_path(source, relative)
            desired = read_metadata(path)
            if desired['kind'] != current['kind']:
                raise ValueError('source/seed inode type differs')
            if current['kind'] == stat.S_IFLNK and os.readlink(path) != current['target']:
                raise ValueError('source/seed symlink target differs')
            rule = 'OS/private/var/' + relative
        except FileNotFoundError:
            # ponytail: only generator-owned paths; new install-state families need review.
            desired = {k: current[k] for k in ('uid', 'gid', 'mode', 'kind')}
            first = relative.split('/')[0]
            if ((relative == '.fseventsd' and current['kind'] == stat.S_IFDIR) or
                    (relative.startswith('.fseventsd/') and relative.count('/') == 1 and
                     current['kind'] == stat.S_IFREG and
                     (relative.split('/')[1] == 'fseventsd-uuid' or
                      (len(relative.split('/')[1]) == 16 and
                       all(c in '0123456789abcdef' for c in relative.split('/')[1]))))):
                rule = 'preserve host filesystem-event journal metadata; not donor install state'
            elif first in ('mobile', 'root') and current['kind'] != stat.S_IFLNK:
                desired.update(uid=501 if first == 'mobile' else 0,
                               gid=501 if first == 'mobile' else 0)
                rule = f'generated {first} descendant; retain mode'
            elif relative == 'tmp' and current['kind'] == stat.S_IFDIR:
                desired.update(uid=0, gid=0, mode=0o1777)
                rule = 'generated tmp directory'
            elif (relative in ('keybags', 'containers', 'containers/Bundle',
                              'containers/Data', 'containers/Data/System',
                              'containers/Shared', 'containers/Shared/SystemGroup',
                              'containers/Temp') and current['kind'] == stat.S_IFDIR):
                desired.update(uid=0, gid=0, mode=0o755)
                rule = 'make-data-seed generated container/keybags directory'
            elif relative in ('db/com.apple.xpc.launchd/disabled.plist',
                              'db/com.apple.xpc.launchd/disabled.migrated') and current['kind'] == stat.S_IFREG:
                desired.update(uid=0, gid=0, mode=0o644)
                rule = 'disable-watchdog-seed generated file; contents untouched'
            else:
                unknown.append(dict(path=relative, reason='no source metadata or explicit rule'))
                continue
        except ValueError as error:
            unknown.append(dict(path=relative, reason=str(error)))
            continue
        before = {k: current[k] for k in ('uid', 'gid', 'mode', 'kind')}
        if before != desired:
            changes.append(dict(path=relative, before=before, after=desired, rule=rule))
    return changes, unknown


def plist_command(*args):
    return plistlib.loads(subprocess.check_output(args))


def mount_identity(mount, raw, readonly):
    info = plist_command('diskutil', 'info', '-plist', str(mount))
    mount_readonly = bool(os.statvfs(mount).f_flag & os.ST_RDONLY)
    images = plist_command('hdiutil', 'info', '-plist')['images']
    matches = [image for image in images if Path(image['image-path']).resolve() == raw.resolve()
               and any(e.get('dev-entry') == info['DeviceNode']
                       for e in image.get('system-entities', []))]
    if (len(matches) != 1 or info.get('VirtualOrPhysical') == 'Physical' or
            info.get('FilesystemType', '').lower() != 'apfs' or
            info.get('GlobalPermissionsEnabled') is not True or
            mount_readonly is not readonly):
        diagnostic = dict(image_matches=len(matches), expected_readonly=readonly,
                          mount_readonly=mount_readonly,
                          info={key: info.get(key) for key in (
                              'DeviceNode', 'DeviceIdentifier', 'VirtualOrPhysical',
                              'FilesystemType', 'GlobalPermissionsEnabled',
                              'VolumeReadOnly', 'MediaReadOnly', 'MountPoint')},
                          images=[dict(path=image.get('image-path'),
                                       entities=image.get('system-entities'))
                                  for image in images])
        raise ValueError('not the expected owners-on APFS disk-image mount: ' +
                         json.dumps(diagnostic, sort_keys=True))
    apfs = plist_command('diskutil', 'apfs', 'list', '-plist')
    volumes = [v for c in apfs['Containers'] for v in c.get('Volumes', [])
               if v['DeviceIdentifier'] == info['DeviceIdentifier']]
    if len(volumes) != 1 or 'Roles' not in volumes[0]:
        raise ValueError('cannot establish APFS role')
    return dict(uuid=info['VolumeUUID'].upper(), size=info['TotalSize'],
                roles=sorted(volumes[0]['Roles']))


@contextmanager
def attached(raw, mount, readonly):
    command = ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
               '-plist', '-nobrowse', '-owners', 'on',
               '-mountpoint', str(mount)]
    if readonly:
        command.append('-readonly')
    devices = []
    try:
        response = plist_command(*command, str(raw))
        entities = response.get('system-entities', [])
        devices = [e['dev-entry'] for e in entities if e.get('dev-entry')]
        mounted = [e for e in entities if e.get('mount-point')]
        if (len(mounted) != 1 or Path(mounted[0]['mount-point']) != mount or not devices):
            raise ValueError('unexpected disk-image mounts')
        yield mount_identity(mount, raw, readonly)
    finally:
        if devices:
            subprocess.run(['hdiutil', 'detach', devices[0]], check=True)


def validate_paths(seed, source, output, packer):
    for path in (seed, source, packer):
        regular(path)
    if (os.path.lexists(output) or os.path.lexists(str(output) + '.json') or
            output.resolve() in (seed.resolve(), source.resolve(), packer.resolve())):
        raise ValueError('output and output.json must be NEW paths')
    if not output.parent.is_dir():
        raise ValueError('output parent must exist')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--os-image', type=Path, required=True)
    parser.add_argument('--packer', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.geteuid() != 0:
        parser.error('requires macOS root; no images opened')
    validate_paths(args.input, args.os_image, args.output, args.packer)
    seed_hash, source_hash = digest(args.input), digest(args.os_image)
    if (seed_hash, source_hash) != (SEED_SHA, OS_SHA):
        parser.error('exact verified watchdog seed and D94 OS raw SHA256 required')
    spec = importlib.util.spec_from_file_location('packed_ramdisk', args.packer)
    packer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packer)
    logical, extents = packer.regions(args.input.read_bytes())
    if logical != SIZE:
        parser.error('seed must have logical size 2147483648')
    work = Path(tempfile.mkdtemp(prefix='d94-ownership-', dir=args.output.resolve().parent))
    raw = work / 'data.raw'
    report = dict(input_sha256=seed_hash, os_sha256=source_hash, logical_bytes=logical,
                  ram_only=True, hardware_tested=False, work_directory=str(work),
                  changes=[], applied=[], skipped_unknown=[], status='failed')
    try:
        with raw.open('xb') as stream:
            stream.truncate(logical)
            for offset, data in extents:
                stream.seek(offset)
                stream.write(data)
        seed_mount, os_mount = work / 'data', work / 'os'
        seed_mount.mkdir()
        os_mount.mkdir()
        with attached(args.os_image.resolve(), os_mount, True):
            source = safe_path(os_mount, 'private/var')
            with attached(raw, seed_mount, False) as identity:
                if identity['uuid'] != UUID:
                    raise ValueError('unexpected Data UUID')
                report['identity'] = identity
                before = inventory(seed_mount)
                changes, unknown = plan(seed_mount, source)
                report.update(changes=changes, skipped_unknown=unknown)
                if unknown:
                    raise ValueError('unknown paths; no metadata edits applied: ' +
                                     json.dumps(unknown, sort_keys=True))
                for change in changes:
                    path = safe_path(seed_mount, change['path'])
                    if metadata(path) != change['before']:
                        raise ValueError('inode metadata changed since planning')
                    wanted = change['after']
                    os.lchown(path, wanted['uid'], wanted['gid'])
                    report['applied'].append(change['path'])
                    os.chmod(path, wanted['mode'], follow_symlinks=False)
                    if metadata(path) != wanted:
                        raise ValueError(f'metadata verification failed: {path}')
                after = inventory(seed_mount)
                expected = {p: dict(v) for p, v in before.items()}
                for change in changes:
                    expected[change['path']].update(change['after'])
                if after != expected:
                    raise ValueError('contents, symlinks, or unexpected inode metadata changed')
                if mount_identity(seed_mount, raw, False) != identity:
                    raise ValueError('Data UUID/size/APFS role changed')
        if raw.stat().st_size != SIZE:
            raise ValueError('raw logical size changed')
        # Reattach read-only to verify persisted metadata after the writable detach.
        with attached(raw, seed_mount, True) as persisted:
            differences = inventory_changes(expected, inventory(seed_mount))
            if persisted != identity or differences:
                report['persisted_identity'] = persisted
                report['persisted_differences'] = differences
                raise ValueError('persisted Data verification failed: ' +
                                 json.dumps(dict(identity=persisted, differences=differences), sort_keys=True))
        candidate = work / 'repaired.dbr'
        packed = packer.pack(raw, candidate)
        if packed['bytes'] != SIZE:
            raise ValueError('packed logical size changed')
        report['output_sha256'] = digest(candidate)
        if digest(args.input) != seed_hash or digest(args.os_image) != source_hash:
            raise ValueError('original image hash changed')
        # Exclusive creation also prevents an existing output being overwritten after validation.
        with candidate.open('rb') as src, args.output.open('xb') as dst:
            import shutil
            shutil.copyfileobj(src, dst)
        if digest(args.output) != report['output_sha256']:
            raise ValueError('published output differs from verified candidate')
        report['status'] = 'verified'
    except BaseException as error:
        report['error'] = str(error)
        raise
    finally:
        report['original_hashes_unchanged'] = (digest(args.input) == seed_hash and
                                                digest(args.os_image) == source_hash)
        with Path(str(args.output) + '.json').open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(f'Created verified ownership-only RAM seed: {args.output}')


if __name__ == '__main__':
    main()
