#!/usr/bin/env python3
"""Generate and register the iOS cfg patch (0065) for whichever AdrenoMetal translator checkpoint a UniXNU core records.

The translator's read_pages / mapped_writable code is cfg'd for Linux, macOS and Windows only, so the ios27 bridge does not
compile (E0425 at copied.rs). iOS gets the macOS mach_vm_read_overwrite variant: every macOS cfg attribute in src/copied.rs
and src/host_memory.rs is extended to iOS. For core 8542980 the result equals scripts/patches/metal2vulkan/0065-ios-read-pages.patch.

  gen-ios-bridge-patch.py CORE      (CORE has .cache/gpu-reference/metal2vulkan at the pinned translator commit, clean)

Writes drivers/AdrenoMetal/bridge/patches/0065-ios-read-pages.patch and appends it to PATCHES in
scripts/build-adreno-metal-bridge.py; the translator checkout is left clean at its pin.
"""
import re
import subprocess
import sys
from pathlib import Path

FILES = ('crates/metal2vulkan-runtime/src/copied.rs', 'crates/metal2vulkan-runtime/src/host_memory.rs')
NAME = '0065-ios-read-pages.patch'


def extend(line):
    if not line.lstrip().startswith('#[cfg(') or 'target_os = "macos"' not in line or 'target_os = "ios"' in line:
        return line
    if 'any(' in line:
        return line.replace('target_os = "macos"', 'target_os = "macos", target_os = "ios"')
    return line.replace('target_os = "macos"', 'any(target_os = "macos", target_os = "ios")')


def main(core):
    src = core / '.cache/gpu-reference/metal2vulkan'
    patches = core / 'drivers/AdrenoMetal/bridge/patches'
    script = core / 'scripts/build-adreno-metal-bridge.py'
    text = script.read_text()
    block = re.search(r"PATCHES = \[ROOT / 'drivers/AdrenoMetal/bridge/patches' / name for name in \[(.*?)\]\]", text, re.S)
    names = re.findall(r"'([^']+\.patch)'", block[1])
    assert names and NAME not in names, names
    git = lambda *a, **k: subprocess.run(['git', '-C', str(src), *a], check=True, **k)
    head = subprocess.check_output(['git', '-C', str(src), 'rev-parse', 'HEAD'], text=True).strip()
    for name in names:
        git('apply', str(patches / name))
    git('add', '-A')
    git('-c', 'user.name=ci', '-c', 'user.email=ci@localhost', 'commit', '-qm', 'recorded patches')
    count = 0
    for rel in FILES:
        f = src / rel
        lines = f.read_text().split('\n')
        new = [extend(line) for line in lines]
        count += sum(a != b for a, b in zip(lines, new))
        f.write_text('\n'.join(new))
    diff = subprocess.check_output(['git', '-C', str(src), 'diff'], text=True)
    git('reset', '-q', '--hard', head)
    git('clean', '-fdq')
    print('recorded patches:', ', '.join(names))
    print('cfg attributes extended to iOS:', count)
    if not diff:
        return
    (patches / NAME).write_text(diff)
    last = "    '" + names[-1] + "']]"
    assert text.count(last) == 1, last
    script.write_text(text.replace(last, "    '" + names[-1] + "',\n    '" + NAME + "']]"))
    print(diff)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]).resolve())
