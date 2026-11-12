"""Offline regression model, not kernel C execution or syscall qualification."""
from pathlib import Path
import struct


def decode(raw):
    assert len(raw) == 0xc8
    security, = struct.unpack_from('<I', raw, 0xac)
    if security & ~0x1f01:
        return 'EINVAL', None
    if security:
        return 'ENOTSUP', None
    return None, raw[0xb0] & 0x0e


raw = bytearray(0xc8)
for security in (*range(65536), 0x10000, 0x80000000, 0xffffffff):
    struct.pack_into('<I', raw, 0xac, security)
    error, _ = decode(raw)
    assert error == ('EINVAL' if security & ~0x1f01 else 'ENOTSUP' if security else None)
struct.pack_into('<I', raw, 0xac, 0)
for byte in range(256):
    raw[0xb0] = byte
    error, cached = decode(raw)
    assert error is None and cached == byte & 0x0e
    for asan, tsan in ((0, 0), (0, 1), (1, 0), (1, 1)):
        value = cached | (2 if asan else 0) | (4 if tsan else 0)
        assert value & 8 == byte & 8
        assert bool(value & 2) == (bool(byte & 2) or bool(asan))
        assert bool(value & 4) == (bool(byte & 4) or bool(tsan))
        assert len(f'sanitizer_config=0x{value:x}\0') <= len('sanitizer_config=0xff\0')
patch = (Path(__file__).resolve().parents[1] / 'patches/xnu/native-d94-sanitizer.patch').read_text()
assert 'attr_size ==' not in patch
assert '+\treturn task->unixnu_sanitizer_config;' in patch
assert '+\t\treturn ENOTSUP;' in patch
assert '+_unixnu_task_get_sanitizer_config' in patch
print('PASS: 65539 security words,1024 sanitizer cases,bit8 transport and source guards')
print('NOT TESTED: kernel compilation,copyin faults,task layout,LTO retention,hardware')
