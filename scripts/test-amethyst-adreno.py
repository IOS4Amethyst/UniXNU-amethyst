#!/usr/bin/env python3
"""Host-only negative checks; no firmware, compiler, device or dependencies needed."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('builder', Path(__file__).with_name('build-amethyst-adreno.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def rejects(manifest, message):
    try:
        builder.checked_files(manifest)
    except ValueError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError('accepted invalid input')


with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / 'input'
    path.write_bytes(b'host test only')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {name: {'path': str(path), 'sha256': digest} for name in builder.required_inputs()}
    rejects({}, 'missing manifest inputs')
    rejects(manifest, 'requires the reviewed D94')
    manifest['kernelcache']['sha256'] = builder.D94_SHA256
    rejects(manifest, 'SHA-256 mismatch')
    manifest['kernelcache'] = {'path': 'relative', 'sha256': digest}
    rejects(manifest, 'path must be absolute')
    manifest['kernelcache'] = {'path': str(path), 'sha256': ''}
    rejects(manifest, 'invalid sha256')
    manifest['kernelcache'] = {'path': str(path.with_name('absent')), 'sha256': digest}
    rejects(manifest, 'missing file')
assert 'NativeAdrenoAccelerator.cpp' in builder.required_inputs()
assert not any('Standins' in name or '8550' in name for name in builder.required_inputs())
print('PASS: missing inputs, wrong kernel, changed bytes, relative paths, invalid hashes and missing files rejected')
