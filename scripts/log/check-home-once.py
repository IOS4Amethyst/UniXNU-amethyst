#!/usr/bin/env python3
"""Exercise the actual request callback with mocked boot arguments/logging."""
from pathlib import Path
import subprocess
import tempfile

kit = Path(__file__).resolve().parent
source = (kit / 'AmethystButtonsHid.cpp').read_text()
callback = source.split('bool native_hid_input_requested(void) {', 1)[1].split('\n}\n', 1)[0]
program = '''#include <cassert>
static unsigned enabled, logs;
bool PE_parse_boot_argn(const char *,unsigned *out,unsigned) { *out=enabled;return true; }
void snapshotLog(const char *,...) { ++logs; }
bool native_hid_input_requested(void) {''' + callback + '''
}
int main() {
 for(int i=0;i<100;++i)assert(!native_hid_input_requested());
 enabled=1;
 for(int i=0;i<29;++i)assert(!native_hid_input_requested());
 assert(native_hid_input_requested());
 for(int i=0;i<100;++i)assert(!native_hid_input_requested());
 assert(logs==1);
}
'''
with tempfile.TemporaryDirectory(dir=kit.parents[2] / '.scratch') as work:
    path = Path(work)
    (path / 'check.cpp').write_text(program)
    subprocess.run(['c++', '-Wall', '-Wextra', '-Werror', str(path / 'check.cpp'),
                    '-o', str(path / 'check')], check=True)
    subprocess.run([str(path / 'check')], check=True)
print('PASS: disabled request, delayed single request, no repeats')
