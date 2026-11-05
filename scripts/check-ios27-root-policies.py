#!/usr/bin/env python3
"""Exercise policy generation with synthetic media and the real C verifier."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--verified-root', type=Path, required=True)
parser.add_argument('--library', type=Path, required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='pmgr-root-check-') as directory:
    root = Path(directory)
    private, public = root / 'test.pem', root / 'test-public.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
                    '-out', str(private)], check=True, capture_output=True)
    private.chmod(0o600)
    subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public)], check=True, capture_output=True)
    images = [root / 'OS.raw', root / 'SystemOS.raw']
    for i, image in enumerate(images):
        image.write_bytes(bytes([i + 1]) * (4096 + 512))
    profile = root / 'profile.toml'
    profile.write_text('[ipsw]\nproduct="iPhone15,4"\nbuild="24A446"\n[root]\n' + '\n'.join(
        f'{name}_image_sha256="{hashlib.sha256(image.read_bytes()).hexdigest()}"\n'
        f'{name}_volume_uuid="00000000-0000-4000-8000-00000000000{i + 1}"'
        for i, (name, image) in enumerate(zip(('os', 'systemos'), images))))
    command = [sys.executable, str(Path(__file__).with_name('build-ios27-root-policies.py')),
               '--profile', str(profile), '--verified-root', str(args.verified_root.resolve()),
               '--library', str(args.library.resolve()), '--private-key', str(private), '--public-key', str(public),
               '--os-image', str(images[0]), '--systemos-image', str(images[1]), '--output', str(root / 'policies')]
    subprocess.run(command, check=True, capture_output=True)
    for name in ('OS', 'SystemOS'):
        out = root / 'policies' / name
        report = json.loads((out / 'verification.json').read_text())
        assert report['blocks_verified'] == 2 and report['real_c_signature_and_block_verification']
        assert len((out / 'manifest.header.bin').read_bytes()) == 544
    refused = subprocess.run(command, capture_output=True, text=True)
    assert refused.returncode != 0 and 'Output already exists' in refused.stderr
    images[0].write_bytes(b'wrong image')
    command[-1] = str(root / 'rejected')
    refused = subprocess.run(command, capture_output=True, text=True)
    assert refused.returncode != 0 and 'differs from profile SHA-256' in refused.stderr
    assert not (root / 'rejected').exists()
print('PASS: synthetic two-image signing/C verification, overwrite refusal, and wrong-image rejection')
