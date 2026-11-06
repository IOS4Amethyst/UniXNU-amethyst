#!/usr/bin/env python3
"""Pin the snapshot's opt-in/read-only safety boundaries; not an MMIO test."""
from pathlib import Path
import re

source = Path(__file__).with_name('AmethystTouchSnapshot.cpp').read_text()
code = re.sub(r'//[^\n]*', '', source)
assert '(enabled!=1 && enabled!=2))return' in code
assert 'kIOMapReadOnly' in code
assert 'mapReadOnly(0x0c400000,0x3000)' in code
assert 'mapReadOnly(0x0b400000' not in code
assert 'const unsigned pins[]={0,1,2,3,70,87}' in code
assert code.index('if(!running)') < code.index('mapReadOnly(0xa80000')
assert 'bool running=amethyst_qup0_running(clocks)' in code
assert code.index('if(enabled==2)') < code.index('bool voted=voteClocks(')
assert code.count('"str %w0, [%1]') == 2
assert '!__atomic_exchange_n(&buttonsStarted,true,__ATOMIC_ACQ_REL)' in code
assert 'amethyst-buttons-identity' in code
assert '"ldr %w0, [%1]"' in code
survey=code.split('void surveySmmu(void) {',1)[1].split('\n}\n',1)[0]
assert 'mapReadOnly(0x15000000,0xd0000)' in survey
assert 'const unsigned streams[]={0x563,0x576,0x60}' in survey
assert '"str ' not in survey and 'voteClocks(' not in survey
assert 'count>128' in survey and 'bank<banks' in survey
for forbidden in ('geni_spi_transfer(', 'memcpy(', 'bzero(', 'writeRegister('):
    assert forbidden not in code, forbidden
# The DMA stage runs only after the read-only survey, via its own opt-in flag.
assert 'amethyst_touch_loopback_stage()' in code
assert 'AmethystTouchLoopbackStage.h' in source
# surveySmmu() is entered by amethyst_touch_snapshot(); the DMA stage is its last
# step, so the read-only survey and preflight always run first.
assert code.index('AmethystTouch: sid=%03x matches=%u') < code.index('amethyst_touch_loopback_stage();')
stage = Path(__file__).with_name('AmethystTouchLoopbackStage.cpp').read_text()
stage_code = re.sub(r'//[^\n]*', '', stage)
assert 'extern "C" void snapshotLog(const char *format,...)' in source
assert 'IOLog(' not in stage_code
for result in ('loopback rc=', 'loopback match=', 'loopback buffers released=',
               'loopback buffers quarantined;'):
    assert 'snapshotLog("AmethystTouch: '+result in stage_code
assert 'PE_parse_boot_argn("amethyst-touch-loopback"' in stage_code
assert 'enabled!=1)return' in stage_code
for diagnostic in ('clock wrapper=%08x oversampling=%u source=%u', 'clock setup rc=%d',
                   'dfs dfsr=%08x l0 cfg=%08x m=%08x n=%08x hz=%u',
                   'clock cfg=%08x sel=%08x cpha=%08x cpol=%08x'):
    assert 'snapshotLog("AmethystTouch: '+diagnostic in stage_code
assert 'if(version==0xffffffffu || !sourceHz)return false' in stage_code
# The SPI source rate comes from SE0 DFS level 0, never an assumed constant.
assert 'geni_spi_set_clock(spi,960000,sourceHz,0)' in stage_code
assert '19200000' not in stage_code
assert 'amethyst_dfs_level0_hz(dfsr,perf0,m0,n0)' in stage_code
assert stage_code.index('amethyst_dfs_level0_hz(') < stage_code.index('maps[0]->release();maps[1]->release();')
assert 'if(status)return false' in stage_code
# Regression guards for the first hardware abort and two later wrong-base bugs.
assert 'load32(kSe0' not in stage_code
assert 'mapMemory(kQup0Vote&~4095u,4096,kIOMapInhibitCache)' in stage_code
assert 'static Se smmuContext;smmuContext.base=smmu->getVirtualAddress();' in stage_code
assert 'amethyst_touch_route(Se::read,&smmuContext,' in stage_code
assert 'amethyst_touch_route_commit(Se::read,Se::write,&smmuContext,' in stage_code
idle = stage_code.index('Se::read(&seContext,SE_GENI_STATUS)')
assert stage_code.index('if(attempt==kMaxVoteWait)return') < idle
assert stage_code.index('static Se seContext;seContext.base=se->getVirtualAddress();') < idle
assert idle < stage_code.index('geni_spi_read_info(&spi)')
assert stage_code.index('amethyst_touch_dma_bind(&spi)') < stage_code.index('geni_spi_init(&spi)')
assert stage_code.index('geni_spi_init(&spi)') < stage_code.index('!qualifyClock(&spi,')
assert stage_code.index('!qualifyClock(&spi,') < stage_code.index('amethyst_touch_loopback(&spi,')
# The stage must prove engine idle before returning buffers to prepared.
assert stage_code.index('result.idle') < stage_code.index('amethyst_touch_dma_idle()')
assert 'quarantined' in stage_code
print('PASS: mode2-only clock write, read-only snapshot, Amethyst pins, clock-gated SE access, '
      'survey-before-loopback stage')
