#!/usr/bin/env python3
"""Regression check using the retained CI IOPMGR object; no compiler required."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import re

spec = importlib.util.spec_from_file_location('audit', Path(__file__).with_name('audit-pmgr-arm64e.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
source = Path(sys.argv[1])
table = audit.object_table(source, '__ZTV6IOPMGR')
assert len(table) == 177
assert table[7][1] == (0, 0xfffc, 1)
header, guards = audit.abi_headers()
declarations = re.findall(r' virtual .*? (\w+)\(', header)
assert len(declarations) == 439 - 177
assert declarations[266 - 177] == 'originalProviderFlag'
assert declarations[280 - 177:282 - 177] == ['readAon', 'writeAon']
assert header.index('opaque300') < header.index('class ApplePMGR :') < header.index('opaque301')
assert 'unavailable(266)' not in guards
assert 'unavailable(280)' not in guards and 'unavailable(281)' not in guards
assert guards.count('unavailable(') == 439 - 177 - 3
bridge = Path(__file__).resolve().parents[1] / 'kits/amethyst/ShengPlatformPower.cpp'
source_text = bridge.read_text()
assert source_text.index('installOriginalPMGRTable(provider);') < source_text.index('!provider->init()')
try:
    audit.pac_header(table, [])
except ValueError as error:
    assert 'requires the rebuilt 439-slot provider' in str(error)
else:
    raise AssertionError('Generated PAC installer for incomplete table')
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'wrong-target.o'
    data = bytearray(source.read_bytes())
    struct.pack_into('<I', data, 8, 0)  # Plain arm64 must not pass this audit.
    path.write_bytes(data)
    try:
        audit.object_table(path, '__ZTV6IOPMGR')
    except ValueError as error:
        assert 'expected arm64e MH_OBJECT' in str(error)
    else:
        raise AssertionError('Accepted plain arm64 object')
    path.write_bytes(source.read_bytes()[:32])
    try:
        audit.object_table(path, '__ZTV6IOPMGR')
    except (ValueError, struct.error):
        pass
    else:
        raise AssertionError('Accepted truncated object')
print('PASS: real arm64e vtable parsed; wrong architecture and truncated object rejected; generated ABI slot boundaries checked')
