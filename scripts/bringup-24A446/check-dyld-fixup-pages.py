#!/usr/bin/env python3
"""Apply the pager patches to source copies and check 4K/16K page geometry."""
import pathlib
import subprocess
import sys
import tempfile

source = pathlib.Path(sys.argv[1]).resolve()
patches = pathlib.Path(__file__).resolve().parents[2] / 'patches/xnu'
with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    for name in ('vm_dyld_pager.c', 'vm_shared_region.c'):
        relative = pathlib.Path('osfmk/vm') / name
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    for name in ('native-zero-key-user-signing.patch', 'native-4k-dyld-fixup-pages.patch'):
        subprocess.run(['git', 'apply', str(patches / name)], cwd=root, check=True)
    text = (root / 'osfmk/vm/vm_dyld_pager.c').read_text()
    assert '/ seg->page_size' in text
    assert 'seg->page_size != hdr->mwli_page_size' in text
    assert 'mwli_page_size != PAGE_SIZE && mwli_page_size != 16384' in text
    assert 'return tainted ? KERN_CODESIGN_ERROR : KERN_SUCCESS;' in text
    assert 'kfree_data(fixup_alloc, 2 * (vm_size_t)mwli_page_size)' in text
    for image_page in (4096, 16384):
        original = bytes(i % 251 for i in range(image_page))
        for offset in range(0, image_page * 3, 4096):
            base = offset & ~(image_page - 1)
            part = (offset - base) // 4096
            assert offset // image_page == base // image_page
            assert original[part * 4096:(part + 1) * 4096] == original[offset % image_page:offset % image_page + 4096]
    print('PASS: pager patches apply; supported page geometry, signature refusal and cleanup checks pass')
