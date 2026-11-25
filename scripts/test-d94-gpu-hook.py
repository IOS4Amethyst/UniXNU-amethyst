#!/usr/bin/env python3
"""Runnable host regression: exact source application, failure atomicity, gates."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import sys

script = Path(__file__).with_name('prepare-d94-gpu-hook.py')
spec = importlib.util.spec_from_file_location('hook', script)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
core = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / 'UniXNU'
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    bsd = root / 'bsd/kern/bsd_init.c'
    kext = root / 'libkern/c++/OSKext.cpp'
    bsd.parent.mkdir(parents=True)
    kext.parent.mkdir(parents=True)
    bsd.write_text((core / 'out/feeds/xnu-3199e8bc/bsd/kern/bsd_init.c').read_text())
    kext.write_text((core / 'out/feeds/xnu-3199e8bc/libkern/c++/OSKext.cpp').read_text())
    # Reproduce the existing offline-legacy-start and RELEASE-display boundary.
    text = bsd.read_text()
    assert text.count('\treturn IOKitBSDInit();') == 1
    text = text.replace('\treturn IOKitBSDInit();', '''\tkern_return_t result = IOKitBSDInit();
#if defined(XNU_NATIVE_LEGACY_DISPLAY) && XNU_NATIVE_LEGACY_DISPLAY
\t/* The original IOSurface personality requires the genuine IOBSD resource.
\t * The default-off framebuffer instrument may start only after publication.
\t */
\textern void native_legacy_display_late_probe(void);
\tnative_legacy_display_late_probe();
#endif
\treturn result;''')
    bsd.write_text(text)
    before = (bsd.read_bytes(), kext.read_bytes())
    hook.apply(root, check=True)
    assert before == (bsd.read_bytes(), kext.read_bytes())
    hook.apply(root)
    patched = bsd.read_text()
    assert patched.index('IOKitBSDInit();') < patched.index('sheng_gpu_power_probe();')
    assert 'result == KERN_SUCCESS &&' in patched
    assert 'unsigned gpu_mode = 0;' in patched
    assert '&& gpu_mode)' in patched
    assert '!(DEVELOPMENT || DEBUG)' in patched
    registered = kext.read_text()
    assert 'result = OSMetaClass::postModLoad(handle);' in registered
    after = (bsd.read_bytes(), kext.read_bytes())
    try:
        hook.apply(root)
        raise AssertionError('duplicate application accepted')
    except ValueError:
        pass
    assert after == (bsd.read_bytes(), kext.read_bytes())
    bsd.write_bytes(before[0])
    kext.write_text('unsupported source\n')
    try:
        hook.apply(root)
        raise AssertionError('partial application accepted')
    except subprocess.CalledProcessError:
        pass
    assert bsd.read_bytes() == before[0]
print('PASS: exact-source hook, default-off guards, registration, duplicate and atomic failure checks')
