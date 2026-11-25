"""Offline check: the five iOS 27-only donor imports bind to native D94 shims, not a boot test.

The strict GPU full-link prelink (run 44780948861) left exactly these five donor kernel
targets unresolved; none has a native twin in xnu-12377. Each must have a reviewed
entry naming a shim the patch defines and exports. Pass a donor kernelcache to also
re-check each entry's recorded size/hash against its bytes.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
patch = (root / 'patches/xnu/native-d94-ios27-shims.patch').read_text()
additions = json.loads((root / 'scripts/d94-reviewed-additions.json').read_text())
prepare = (root / 'scripts/prepare-d94-build.py').read_text()

EXPECTED = {
    '0xfffffff00b11beac': '_unixnu_d94_image4_unavail_beac',
    '0xfffffff00b11bf24': '_unixnu_d94_image4_unavail_bf24',
    '0xfffffff00b11bf90': '_unixnu_d94_image4_unavail_bf90',
    '0xfffffff00b11bfb0': '_unixnu_d94_image4_unavail_bfb0',
    '0xfffffff00b2ab224': '_unixnu_d94_ioservice_rpc_bf66',
}
entries = {x['original']: x for x in additions['functions'] if x['original'] in EXPECTED}
assert set(entries) == set(EXPECTED), 'missing reviewed shim entry'
added = [line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++')]
exports = {line for line in added if line.startswith('_unixnu_d94_')}
for original, symbol in EXPECTED.items():
    entry = entries[original]
    assert entry['native_symbol'] == symbol and entry['evidence'], original
    assert symbol in exports, 'not exported: ' + symbol
    # One non-static definition per shim, kept through LTO.
    body = '\n'.join(added)
    assert len(re.findall(r'^(?:extern "C" __attribute__\(\(used, noinline\)\) IOReturn\n|uint32_t\n)'
                          + re.escape(symbol[1:]) + r'\(', body, re.M)) == 1, 'definition: ' + symbol
assert len(set(EXPECTED.values())) == len(EXPECTED)
assert '+\treturn kIOReturnUnsupported;' in patch and '+\t\t*result = NULL;' in patch
assert patch.count('+\treturn 0xffff;') == 4 and patch.count('+\t\treturn 3;') == 2
assert 'native-microuptime-precision.patch\\nnative-d94-ios27-shims.patch\\n' in prepare
if len(sys.argv) > 1:
    raw = Path(sys.argv[1]).read_bytes()
    base = 0xfffffff007004000
    for original, entry in entries.items():
        start = int(original, 16) - base
        digest = hashlib.sha256(raw[start:start + entry['original_size']]).hexdigest()
        assert digest == entry['original_sha256'], 'donor bytes: ' + original
print('PASS: five iOS 27-only donor imports reviewed against exported native D94 shims; series wired')
