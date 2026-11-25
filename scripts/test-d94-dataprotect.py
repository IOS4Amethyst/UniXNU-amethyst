"""Offline check: sheng's owner-dataprotect VFS emulation (plus class-carrying creates) is wired into the D94 build.

An unencrypted owner Data volume has no content protection, so APFS refuses class opens
("rejecting class open (class 2) because we're not content protected", seen on b14 hardware).
With boot-arg owner-dataprotect=1 the class is kept in an xattr instead. Not a boot test.
"""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
patch = (root / 'patches/xnu/native-d94-dataprotect.patch').read_text()
prepare = (root / 'scripts/prepare-d94-build.py').read_text()
gpu = (root / 'scripts/prepare-d94-gpu-link.py').read_text()
workflow = (root / '.github/workflows/ios27-kernel.yml').read_text()

assert patch.startswith('diff --git a/bsd/vfs/kpi_vfs.c b/bsd/vfs/kpi_vfs.c')
for needle in ('"owner-dataprotect"', '"com.unixnu.dataprotect-class"', 'native_dataprotect_getattr(vp, vap, ctx);',
               'native_dataprotect_setattr(vp, vap, ctx);', 'VATTR_CLEAR_ACTIVE(vap, va_dataprotect_class);',
               'native_dataprotect_setattr(*vpp, &native_dp_va, ctx);', '!(vfs_flags(mp) & MNT_CPROTECT)'):
    assert needle in patch, needle
assert 'native-d94-ios27-shims.patch\\nnative-d94-dataprotect.patch\\n' in prepare
assert "selected in ('amethyst_defconfig', 'ios27_data_defconfig')" in gpu
assert 'test "$DEFCONFIG" = amethyst_defconfig || test "$DEFCONFIG" = ios27_data_defconfig' in workflow
print('PASS: owner-dataprotect emulation patch wired; GPU relink accepts the Data defconfig')
