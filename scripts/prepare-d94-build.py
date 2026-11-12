#!/usr/bin/env python3
"""Prepare a disposable CI checkout for a kernel-only D94 migration audit."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import urllib.request

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--core', type=Path, required=True)
p.add_argument('--device', type=Path, required=True)
a = p.parse_args()
if not (a.core / 'kits/XnuPrelink/fileset_match.py').is_file():
    raise SystemExit('Initialize the pinned kits/XnuPrelink submodule before preparing the donor fixture')
name = 'ios-27.0.1-24A446-d94ap'
url = 'https://raw.githubusercontent.com/MoyuZJ912/UniXNU-aston/'
patch = urllib.request.urlopen(url + '282c1d672373a11ef69810e36723fc6ae807f004/unixnu-patches/0001-ios-27.0.1-24A446-iphone17-2.patch', timeout=60).read().decode()
prefix = 'profiles/os/ios-27.0.1-24A446/'
written = []
for section in patch.split('diff --git ')[1:]:
    lines = section.splitlines()
    target = next(line[6:] for line in lines if line.startswith('+++ b/'))
    if not target.startswith(prefix):
        continue
    assert '--- /dev/null' in lines
    relative = target[len(prefix):]
    assert relative in ('profile.toml', 'configs/ios.config', 'patches/xnu/series', 'abi/original-classes.json')
    text = '\n'.join(line[1:] for line in lines if line.startswith('+') and not line.startswith('+++')) + '\n'
    if relative == 'profile.toml':
        text = text.replace('"name" = "ios-27.0.1-24A446"', f'"name" = "{name}"')
    dest = a.core / 'profiles/os' / name / relative
    assert not dest.exists(), f'refuse overwrite: {dest}'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    written.append(relative)
assert len(written) == 4
raw = urllib.request.urlopen(url + '27973a724c8faf2f5bbf0bb9e7e29992e8f6ac67/scripts/fileset-24A446-d94ap-symbols.json', timeout=60).read()
assert hashlib.sha256(raw).hexdigest() == '377e220a2ab1d7d03265b6049a48dcf0273d6154cf70672e0ea9f7eaa87a07b0'
f = json.loads(raw)
assert f['inputs']['kernelcache_sha256'] == 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
assert (len(f['functions']), len(f['data'])) == (172, 22)
# Bitmask getter is NOT native boolean task_is_privileged. Four data pins are
# outside com.apple.kernel. Keep these unresolved rather than silently misbind.
f['functions'] = [x for x in f['functions'] if x['original'] != '0xfffffff00ac11bd8']
bad = {'0xfffffff007ec40c0', '0xfffffff007ec40c8', '0xfffffff007ec4150', '0xfffffff007ef16c8'}
f['data'] = [x for x in f['data'] if x['original'] not in bad]
assert (len(f['functions']), len(f['data'])) == (171, 18)
(a.core / 'kits/XnuPrelink/fixtures/fileset-24A446-d94ap-symbols.json').write_text(json.dumps(f, indent=2) + '\n')
board = a.device / 'board.toml'
t = board.read_text()
needle = '"ios-27.0.1-24A446" = "amethyst_defconfig"'
assert t.count(needle) == 1
t = t.replace(needle, needle + f'\n"{name}" = "amethyst_defconfig"')
needle = '"os:configs/ipados.config"'
assert t.count(needle) == 1
t = t.replace(needle, '"os:configs/ios.config"')
needle = '  "component:AppleKeyStoreState",'
assert t.count(needle) == 1
t = t.replace(needle, needle + '\n  "build-fileset-release-reference.py --tree . --lto",')
t = t.replace('root_match_volume_uuid = "E89DE0E4-B66C-425D-BCD1-90F637F43F1F"', 'root_match_volume_uuid = "E3F4AED0-4454-4D5B-833C-E27E99C4F67B"')
b = tomllib.loads(t)
assert b['board']['soc'] == 'SM7635' and b['payload']['adt']['persistent_data'] is False
assert 'sheng-data-persist=0' in b['payload']['kernel_args']
board.write_text(t)
series = a.device / 'patches/xnu/series'
text = series.read_text()
assert 'native-kernel-stack-shift.patch' not in text
series.write_text(text.rstrip() + '\nnative-kernel-stack-shift.patch\n')
print('PASS: D94 kernel-only checkout;171function/18data anchors; SM7635/RAM-only retained')
print('NOT PAYLOAD READY: existing compiled root policies are iPhone15,4; donor signing remains required')
