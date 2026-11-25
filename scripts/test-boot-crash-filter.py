#!/usr/bin/env python3
"""Compile the real bounded crash-report filter; no kernel or device required."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    source = Path(directory) / 'check.c'
    source.write_text('''#include <assert.h>
#include <stdio.h>
#include "BootLatencyFiles.h"
int main(void) {
 unsigned counters[BL_CRASH_PROCESS_COUNT]={0};
 for(unsigned i=0;i<BL_CRASH_PROCESS_COUNT;++i) {
  char path[256];
  snprintf(path,sizeof path,"/private/var/mobile/Library/Logs/CrashReporter/%s-2026.ips",bl_crash_processes[i]);
  assert(bl_latency_file(path));assert(bl_crash_process(path)==(int)i);++counters[i];
 }
 assert(!bl_latency_file("/private/var/mobile/SpringBoard-2026.ips"));
 assert(!bl_latency_file("/private/var/mobile/Library/Logs/CrashReporter/SpringBoardFake-2026.ips"));
 assert(bl_crash_process("/x/com.apple.datamigrator-2026.ips")>=0);
 assert(BL_CRASH_PROCESS_COUNT==9);
 return 0;
}
''')
    binary = Path(directory) / 'check'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-I', str(root / 'package'), str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
assert 'crash_count[BL_CRASH_PROCESS_COUNT]' in (root / 'package/BootDiagExport.c').read_text()
print('PASS: nine real crash filters, counter bounds, path/name refusals; compiled UBSan')
