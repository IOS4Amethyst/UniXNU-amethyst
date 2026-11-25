#!/usr/bin/env python3
"""Read-only D94 fixture/source check. Run with UNIXNU_KERNELCACHE and UNIXNU_CORE set."""
import ast
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

board = Path(__file__).resolve().parents[1]
core = Path(os.environ['UNIXNU_CORE']).resolve()
sys.path.insert(0, str(core / 'kits/XnuPrelink'))
from kc_image import Image

abi = json.loads((board / 'scripts/legacy-display-24A446.json').read_text())
raw = Path(os.environ['UNIXNU_KERNELCACHE']).read_bytes()
assert hashlib.sha256(raw).hexdigest() == abi['kernelcache_sha256'] == 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
assert len(abi['slots']) == 379 and int(abi['collection_slide'], 16) == 0
image = Image(raw)
fixups = image.fixups()
differences = []
for i, (target, diversity) in enumerate(abi['slots']):
    f = fixups[int(abi['vtable'], 16) + i * 8]
    assert f[0] == int(target, 16), (i, f, target)
    assert f[1] == 1 and f[2] == 0 and f[4] == 1, (i, f)
    if f[3] != int(diversity, 16):
        differences.append(i)
assert differences == [214, 216, 257], differences  # Reviewed pure-virtual caller diversities.
builder = (board / 'scripts/build-gop-legacy-display-27.py').read_text()
# Exercise the actual builder's discriminator without executing its build side effects.
tree = ast.parse(builder)
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'discriminator')
scope = {'struct': struct}
exec(compile(ast.Module(body=[function], type_ignores=[]), '<discriminator>', 'exec'), scope)
for base, (name, suffix) in abi['names'].items():
    slot = abi['methods'].get(base)
    if slot is None:
        slot = int(base[len('opaque'):])
    assert scope['discriminator'](f'_ZN25IOMobileFramebufferLegacy{len(name)}{name}{suffix}') == int(abi['slots'][slot][1], 16)
assert 'native_slide' not in builder and 'native-slide' not in builder
assert "ROOT / OBJECT_TREE / 'iokit' / CONFIG_LEVEL" in builder
assert 'NativeGpuTrace.h' in builder and 'len(slots) == 379' in builder
assert 'word >> 49 & 3 == 0' in builder and 'pure <= set(owned)' in builder
probe = (board / 'package/LegacyDisplayProbe.cpp').read_text()
assert 'if(gpu || direct)' in probe
for stale in ('bytes+2624', 'bytes+5472', 'bytes+5480', 'object+5820', 'object+5824', 'bytes+5820'):
    assert stale not in probe, stale
print('PASS: exact D94 SHA, 379 targets, IA/address diversity, reviewed discriminator names, fixed adapters, RELEASE recipe and probe guards')
