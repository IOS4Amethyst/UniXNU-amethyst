#!/usr/bin/env python3
"""Apply the signing patch to temporary source copies and check its policy gates."""
import pathlib
import subprocess
import sys
import tempfile

source = pathlib.Path(sys.argv[1]).resolve()
patch = pathlib.Path(__file__).resolve().parents[2] / 'patches/xnu/native-zero-key-user-signing.patch'
with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    for name in ('vm_dyld_pager.c', 'vm_shared_region.c'):
        relative = pathlib.Path('osfmk/vm') / name
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    subprocess.run(['git', 'apply', str(patch)], cwd=root, check=True)
    pager = (root / 'osfmk/vm/vm_dyld_pager.c').read_text()
    shared = (root / 'osfmk/vm/vm_shared_region.c').read_text()
    assert 'pager->dyld_a_key == 0 || arm_user_jop_disabled()' not in pager
    assert 'if (arm_user_jop_disabled())' in pager
    assert 'pmap_sign_user_ptr((void *)unsignedAddr' in pager
    assert 'jop_key != 0 && si->si_ptrauth' not in shared
    assert shared.count('if (si->si_ptrauth && !arm_user_jop_disabled())') == 3
    print('PASS: zero software key IDs no longer bypass signing; explicit PAC policy retained')
