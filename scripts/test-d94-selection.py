"""Run offline: validate the active donor selection, not a firmware/boot test."""
import hashlib
import json
from pathlib import Path
import tomllib

root = Path(__file__).resolve().parents[1]
profile = tomllib.loads((root / 'profiles/os/ios-27.0.1-24A446-d94ap/profile.toml').read_text())
assert profile['ipsw']['product'] == 'iPhone17,2'
assert profile['ipsw']['device_class'] == 'd94ap'
assert profile['kernel']['prelink']['kernelcache_sha256'] == 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
assert profile['root']['os_volume_uuid'] == 'E3F4AED0-4454-4D5B-833C-E27E99C4F67B'
assert profile['root']['systemos_volume_uuid'] == '8B599D21-9ABD-4050-8BA2-C61349890BED'
assert 'CONFIG_XNU_KERNEL_CHAINED_FIXUPS=y' in (root / 'profiles/os/ios-27.0.1-24A446-d94ap/configs/ios.config').read_text()
assert hashlib.sha256((root / 'scripts/fixtures/fileset-24A446-d94ap-donor.json').read_bytes()).hexdigest() == '377e220a2ab1d7d03265b6049a48dcf0273d6154cf70672e0ea9f7eaa87a07b0'
additions = json.loads((root / 'scripts/d94-reviewed-additions.json').read_text())
assert additions['inputs']['kernelcache_sha256'] == profile['kernel']['prelink']['kernelcache_sha256']
for name in ('ios27-kernel.yml', 'ios27-root-policies.yml'):
    text = (root / '.github/workflows' / name).read_text()
    assert 'options: [d94ap]' in text and 'default: d94ap' in text
    assert 'OS_PROFILE: ios-27.0.1-24A446-d94ap' in text
seed = (root / '.github/workflows/ios27-data-seed.yml').read_text()
assert "product='iPhone17,2'" in seed and "'--gib', '2'" in seed
assert 'c640246d38aaeb2d' not in seed
config = (root / 'configs/ios27_data_defconfig').read_text()
assert 'CONFIG_XNU_OWNER_DATA_MOUNT=n' in config
assert '072899DE-BA32-4875-B776-41C1929F4002' not in config
print('PASS: canonical D94 profile/fixture/root identities/workflow defaults; old Data disabled;2GiB donor seed selected')
