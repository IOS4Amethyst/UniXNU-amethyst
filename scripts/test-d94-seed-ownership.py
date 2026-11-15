#!/usr/bin/env python3
"""Host-only planner/refusal tests; no macOS, root, disk images or mounts needed."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    'repair', Path(__file__).with_name('repair-d94-seed-ownership.py'))
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def refuses(call):
    try:
        call()
    except (ValueError, FileNotFoundError):
        return
    raise AssertionError('unsafe input accepted')


original_plist = repair.plist_command
assert repair.inventory_changes({'same': 1, 'changed': 1, 'removed': 1},
                                {'same': 1, 'changed': 2, 'added': 3}) == [
    dict(path='added', expected=None, actual=3),
    dict(path='changed', expected=1, actual=2),
    dict(path='removed', expected=1, actual=None)]
original_statvfs = repair.os.statvfs
repair.os.statvfs = lambda path: SimpleNamespace(f_flag=os.ST_RDONLY)
def rejected_mount(*args):
    if args[0] == 'hdiutil':
        return {'images': []}
    return {'DeviceNode': '/dev/disk9s1', 'FilesystemType': 'apfs',
            'GlobalPermissionsEnabled': False, 'VolumeReadOnly': True}
repair.plist_command = rejected_mount
try:
    repair.mount_identity(Path('/unused-mount'), Path('/unused.raw'), True)
except ValueError as error:
    assert '"image_matches": 0' in str(error)
    assert '"GlobalPermissionsEnabled": false' in str(error)
else:
    raise AssertionError('unverified mount accepted')
finally:
    repair.plist_command = original_plist
    repair.os.statvfs = original_statvfs


def verified_mount(*args):
    if args[0] == 'hdiutil':
        return {'images': [{'image-path': '/unused.raw',
                            'system-entities': [{'dev-entry': '/dev/disk9s1'}]}]}
    if args[1] == 'apfs':
        return {'Containers': [{'Volumes': [{'DeviceIdentifier': 'disk9s1',
                                             'Roles': ['Data']}]}]}
    return {'DeviceNode': '/dev/disk9s1', 'DeviceIdentifier': 'disk9s1',
            'FilesystemType': 'apfs', 'GlobalPermissionsEnabled': True,
            'VolumeUUID': repair.UUID,
            'TotalSize': repair.SIZE}
repair.plist_command = verified_mount
repair.os.statvfs = lambda path: SimpleNamespace(f_flag=os.ST_RDONLY if mount_readonly else 0)
try:
    for mount_readonly in (True, False):
        identity = repair.mount_identity(Path('/unused-mount'), Path('/unused.raw'), mount_readonly)
        assert identity == dict(uuid=repair.UUID, size=repair.SIZE, roles=['Data'])
        refuses(lambda: repair.mount_identity(Path('/unused-mount'), Path('/unused.raw'), not mount_readonly))
finally:
    repair.plist_command = original_plist
    repair.os.statvfs = original_statvfs


with tempfile.TemporaryDirectory(prefix='test-d94-ownership-') as td:
    work = Path(td)
    seed, source, outside = (work / p for p in ('seed', 'source', 'outside'))
    for p in (seed, source, outside):
        p.mkdir()
    for root in (seed, source):
        (root / 'mobile/Library').mkdir(parents=True)
        (root / 'mobile').chmod(0o711)
        (root / 'mobile/Library/preferences').write_bytes(b'unchanged plist bytes')
        (root / 'link').symlink_to('../outside')
    (outside / 'secret').write_bytes(b'never touched')
    for name in ('root/Library/Preferences', 'mobile/tmp', 'tmp', 'containers/Data/System'):
        (seed / name).mkdir(parents=True)
    (seed / 'root/Library/Preferences/setting').write_bytes(b'root preference')
    (seed / 'unreviewed').write_bytes(b'unknown')
    (seed / 'db/com.apple.xpc.launchd').mkdir(parents=True)
    (seed / 'db/com.apple.xpc.launchd/disabled.plist').write_bytes(b'watchdog policy')
    # Source directory metadata is authoritative even for non-mobile service groups.
    (seed / 'run').mkdir()
    (source / 'run').mkdir()

    def source_metadata(path):
        actual = repair.metadata(path)
        relative = path.relative_to(source).as_posix()
        actual.update(uid=501 if relative.startswith('mobile') else 0,
                      gid=501 if relative.startswith('mobile') else 0)
        if relative == 'run':
            actual.update(uid=0, gid=20, mode=0o775)
        return actual

    before_seed, before_source = repair.inventory(seed), repair.inventory(source)
    secret = repair.inventory(outside)
    changes, unknown = repair.plan(seed, source, source_metadata)
    by_path = {change['path']: change for change in changes}
    assert by_path['mobile']['after']['uid'] == 501
    assert by_path['mobile']['after']['mode'] == 0o711
    assert by_path['mobile/tmp']['after']['gid'] == 501
    assert by_path['run']['after']['gid'] == 20
    assert by_path['tmp']['after']['mode'] == 0o1777
    assert any(u['path'] == 'unreviewed' for u in unknown)
    assert not any('secret' in c['path'] for c in changes)
    assert repair.inventory(seed) == before_seed
    assert repair.inventory(source) == before_source
    assert repair.inventory(outside) == secret

    for relative in ('../outside/secret', '/outside', 'mobile/../run',
                     'mobile//Library', 'mobile/./Library', ''):
        refuses(lambda relative=relative: repair.safe_path(seed, relative))
    refuses(lambda: repair.safe_path(seed, 'link/secret'))
    assert repair.safe_path(seed, 'link').is_symlink()
    # No fallback rules through a source symlink (including mobile descendants).
    (source / 'mobile/Library/preferences').unlink()
    (source / 'mobile/Library').rmdir()
    (source / 'mobile/Library').symlink_to(outside)
    changes, unknown = repair.plan(seed, source, source_metadata)
    assert any(u['path'] == 'mobile/Library/preferences' and 'symlink ancestor' in u['reason']
               for u in unknown)
    assert not any(c['path'] == 'mobile/Library/preferences' for c in changes)
    # A mismatched final symlink target is also refused, not "repaired".
    (source / 'link').unlink()
    (source / 'link').symlink_to('/different')
    _, unknown = repair.plan(seed, source, source_metadata)
    assert any(u['path'] == 'link' and 'target differs' in u['reason'] for u in unknown)
    assert repair.inventory(seed) == before_seed
    assert repair.inventory(outside) == secret

    for name in ('input', 'os.raw', 'packer.py'):
        (work / name).write_bytes(name.encode())
    args = work / 'input', work / 'os.raw', work / 'output', work / 'packer.py'
    repair.validate_paths(*args)
    refuses(lambda: repair.validate_paths(args[0], args[1], args[0], args[3]))
    (work / 'output.json').write_text('existing report')
    refuses(lambda: repair.validate_paths(*args))
    (work / 'output.json').unlink()
    (work / 'input-link').symlink_to(args[0])
    refuses(lambda: repair.validate_paths(work / 'input-link', *args[1:]))
    (work / 'output').symlink_to(work / 'missing')
    refuses(lambda: repair.validate_paths(*args))
    refuses(lambda: repair.regular(seed))
    os.link(work / 'input', source / 'source-hardlink')
    assert repair.source_metadata(source / 'source-hardlink')['uid'] == os.getuid()
    refuses(lambda: repair.metadata(source / 'source-hardlink'))
    (seed / '.fseventsd').mkdir()
    for name in ('0000000008edbd2b', 'fseventsd-uuid', 'unreviewed'):
        (seed / '.fseventsd' / name).write_bytes(b'journal unchanged')
    changes, unknown = repair.plan(seed, source)
    assert not any(c['path'].startswith('.fseventsd') for c in changes)
    journal_unknown = [u['path'] for u in unknown if u['path'].startswith('.fseventsd')]
    assert journal_unknown == ['.fseventsd/unreviewed']
    os.link(work / 'input', seed / 'hardlink1')
    os.link(work / 'input', seed / 'hardlink2')
    refuses(lambda: repair.inventory(seed))

print('PASS: source-backed planner, scoped rules, unknown refusal, unchanged inputs, '
      'symlink escape/target refusal, hardlink and output validation')
