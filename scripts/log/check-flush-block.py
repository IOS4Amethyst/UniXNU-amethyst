#!/usr/bin/env python3
"""Pin the flush's full-block source; a stack header is shorter than 4096 bytes."""
from pathlib import Path

source = Path(__file__).with_name('BootLog.c').read_text()
flush = source.split('int amethyst_boot_log_flush(', 1)[1].split('\n}', 1)[0]
steps = [
    'native_ufs_log_write(LogMsgFirst, LogMsgBlocks, boot_log_panic_buffer)',
    'memset(boot_log_panic_buffer, 0, LogBlock)',
    'memcpy(boot_log_panic_buffer, &h, sizeof h)',
    'native_ufs_log_write(0, 1, boot_log_panic_buffer)',
]
positions = [flush.index(step) for step in steps]
assert positions == sorted(positions)
assert '(const uint8_t *)&h' not in flush
assert 'IOMallocAligned(LogMsgBlocks * LogBlock, LogBlock)' in source
print('PASS: flush writes its header from a zero-padded allocated block')
