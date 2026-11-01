#!/usr/bin/env python3
"""Check diagnostic publication owns a reference before readers can see it."""
from pathlib import Path

root = Path(__file__).resolve().parent
source = (root / 'AmethystDisplayProbe.cpp').read_text()
allocation = source.index('IOService *display=native_legacy_display_allocate();')
retain = source.index('display->retain();', allocation)
publish = source.index('__atomic_store_n(&native_legacy_object', allocation)
assert allocation < retain < publish
assert source.count('display->retain();') == 1
assert source.count('display->release();') == 2  # alternative local-owner exits
assert 'if(nub)nub->retain();' in source
assert source.count('__atomic_load_n(&native_legacy_object,__ATOMIC_ACQUIRE)') == 3
reader = source.split('unsigned native_legacy_state_words(', 1)[1].split('\n}', 1)[0]
assert 'native_legacy_object_size<5872' in reader
assert reader.index('if(!out || !cap)') < reader.index('out[0]=0')
assert 'display inactive; skipped reads=%u' in reader
assert '__atomic_add_fetch(&native_legacy_released_reads' in reader
print('PASS: boot-long diagnostic references, acquire publication, bounded state reader and skip counter')
