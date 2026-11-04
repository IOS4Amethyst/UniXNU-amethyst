#!/usr/bin/env python3
"""Pin the Goodix stage's opt-in gate and power ordering; not a hardware test."""
from pathlib import Path
import re

here = Path(__file__).parent
strip = lambda t: re.sub(r'//[^\n]*', '', t)
stage = strip((here / 'AmethystTouchGoodixStage.cpp').read_text())
loop = strip((here / 'AmethystTouchLoopbackStage.cpp').read_text())
power = (here / 'AmethystTouchPower.h').read_text()

assert 'PE_parse_boot_argn("amethyst-touch-goodix",&enabled,sizeof enabled) || enabled!=1' in stage
# Runs only after a matched loopback that returned its buffers.
released = loop.index('loopback buffers released=%d')
call = loop.index('amethyst_touch_goodix_stage(&spi)')
assert released < call and 'result.matched && amethyst_touch_goodix_stage(&spi)' in loop
assert loop.index('result.idle && !result.transferStatus') < call
# Board rails, not Asteroids' ldob22/ldob11.
assert '"ldob13"' in power and '"ldob23"' in power and 'ldob22' not in power + stage
assert 'AmethystPinIrq = 70, AmethystPinReset = 87' in power
# Linux goodix_berlin_power_on: reset low -> iovdd -> avdd -> bus ready (SPI mux, buffers)
# -> reset high -> 4 ms -> dev confirm write/read -> 100 ms -> version -> IC_INFO.
order = ['wr(reset.va,AmethystTlmmIo,0)', 'AmethystIovddMv', 'AmethystAvddMv',
         'amethyst_spi_pins[i].ctl)', 'amethyst_touch_dma_claim()', 'wr(reset.va,AmethystTlmmIo,2)',
         'IOSleep(AmethystResetSettleMs)', 'amethyst_goodix_write(spi,GoodixBerlinBootOptionAddress',
         'IOSleep(AmethystFirmwareBootMs)', 'GoodixBerlinVersionAddress', 'GoodixBerlinIcInfoAddress']
at = [stage.index(s, stage.index('extern "C" int amethyst_touch_goodix_stage')) for s in order]
assert at == sorted(at), list(zip(order, at))
assert 'aRc=ioRc?-125:' in stage          # no avdd without iovdd
# The only write is the boot option handshake; no firmware or mode commands.
assert 'geni_spi_transfer(' not in stage and 'GoodixBerlinWrite' not in stage
assert stage.count('amethyst_goodix_write(') == 3   # declaration, the handshake, the command sender
# Commands: only goodix_htc_enable(0) (raw off, coordinates on), only with the event poller.
assert stage.count('sendCommand(spi,') == 2
assert 'GoodixBerlinCmdTouchRaw,0,' in stage and 'GoodixBerlinCmdTouchData,1,' in stage
assert stage.index('icInfo.cmdAddress && wantEvents') < stage.index('GoodixBerlinCmdTouchRaw,0,')
assert 'memset(confirm,GoodixBerlinConfirmValue,sizeof confirm)' in stage
assert stage.count('amethyst_goodix_read(') == 6   # declaration + echo, version, IC_INFO length and block, cmd ack
assert 'return -16;' in stage and 'buffers quarantined' in stage
# At most seven retained rows from this stage on any single path.
# 48-row log: loopback success path leaves 5 rows; this stage uses 3 (+1 quarantine).
body = stage.split('extern "C" int amethyst_touch_goodix_stage',1)[1]
# stop-before-power | rails, pins+irq, (no buffers | id OK | id FAIL), quarantine
assert body.count('snapshotLog(') == 7, body.count('snapshotLog(')
print('PASS: opt-in after matched loopback, Amethyst rails/pins, Linux power-on order with dev confirm, IC_INFO, htc_enable(0) only')
