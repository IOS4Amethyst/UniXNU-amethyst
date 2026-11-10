#!/usr/bin/env python3
"""Apply the reference user-VA patch to source copies and check PAC/ISA geometry."""
import pathlib
import subprocess
import sys
import tempfile

source = pathlib.Path(sys.argv[1]).resolve()
patch = pathlib.Path(__file__).resolve().parents[2] / 'patches/xnu/native-arm64e-user-va.patch'
text = patch.read_text()
files = [line[6:] for line in text.splitlines() if line.startswith('+++ b/')]
with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    for name in files:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / name).read_bytes())
    subprocess.run(['git', 'apply', str(patch)], cwd=root, check=True)
    reg = (root / 'osfmk/arm64/proc_reg.h').read_text()
    assert '#define T0SZ_USER 17ULL' in reg and '#define TCR_EL1_KVA_ENTRY' in reg
    assert 'TCR_EPD0_DISABLE' in reg
    pmap = (root / 'osfmk/arm/pmap/pmap.c').read_text()
    assert '.pta_root_level = (T0SZ_USER - 16) / 9' in pmap
    assert 'set_mmu_ttb(invalid_ttep & TTBR_BADDR_MASK);' in pmap
    assert 'pmap_apply_user_tcr();' in pmap
    assert (17 - 16) // 9 == 0  # four-level 4K user root
    assert (1 << (64 - 17 - 39)) * 8 == 2048  # root allocation: 256 entries
    isa_mask = 0x7fffffffff8
    bad = 0x501e0172bf0
    assert bad & ~((1 << 39) - 1) and bad & isa_mask == bad
    # With a 47-bit VA range, PAC bits cannot survive the 43-bit ISA mask.
    pac_region = ((1 << 55) - 1) ^ ((1 << 47) - 1)
    assert pac_region & isa_mask == 0
    print('PASS: reference patch applies; user L0 and ISA/PAC mask geometry consistent')
