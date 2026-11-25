// SPDX-License-Identifier: BSD-2-Clause-Patent
// Goodix GT9916R power-up plan for Amethyst. Board facts only:
//   amethyst-sm7635-xiaomi-touch.dtsi: avdd L13B 3.224V, iovdd L23B 1.8V,
//     reset GPIO87 (flags 0: high = run, 8mA), irq GPIO70 (input, no bias),
//     SPI GPIO0..3 qup0_se0_l0..l3, drive 6/4/4/6mA, no bias.
//   volcano-regulators.dtsi: L13B = ldob13, L23B = ldob23.
//   volcano.dtsi: apps_rsc drv-2 at 0x17a20000, tcs-offset 0xd00, active TCS 0..2.
//   volcano-reserved-memory.dtsi: cmd-db at 0x81c60000, 128 KiB.
// TLMM layout, mux function 1 for qup0_se0 and the RPMh VRM command order
// (mode +8, millivolts +0, enable +4) follow rootstockos/UniXNU-asteroids
// AsteroidsTouchProbe.cpp, proven on SM7635. Rail order and settle times are
// the Goodix Berlin vendor sequence: iovdd, 3ms, avdd, 15ms, reset release,
// then 100ms for firmware boot.
#pragma once
#include <stdint.h>

enum {
    AmethystTlmmBase = 0xf100000,          // DT 0xf000000 + REG_BASE 0x100000
    AmethystTlmmStride = 0x1000,
    AmethystTlmmCtl = 0, AmethystTlmmIo = 4,
    AmethystPinMiso = 0, AmethystPinMosi = 1, AmethystPinClk = 2, AmethystPinCs = 3,
    AmethystPinIrq = 70, AmethystPinReset = 87,
    AmethystQup0Se0Function = 1,
    AmethystIovddMv = 1800, AmethystAvddMv = 3224,
    AmethystIovddSettleMs = 3, AmethystAvddSettleMs = 15,
    AmethystResetSettleMs = 4, AmethystFirmwareBootMs = 100,
};
#define AMETHYST_CMD_DB_BASE 0x81c60000ull
#define AMETHYST_CMD_DB_BYTES 0x20000u
#define AMETHYST_RSC_DRV2 0x17a20000ull
#define AMETHYST_RSC_TCS_OFFSET 0xd00u
#define AMETHYST_RSC_ACTIVE_TCS 2u
#define AMETHYST_AVDD_RESOURCE "ldob13"
#define AMETHYST_IOVDD_RESOURCE "ldob23"

// TLMM CTL: pull 1:0, function 5:2, drive 8:6 ((mA/2)-1), output enable 9.
// Returns 0xffffffff for values the block cannot encode (drive 2..16mA, even).
static inline uint32_t amethyst_tlmm_ctl(unsigned function,unsigned milliamps,
    unsigned pull,int output) {
    if(function>15 || pull>3 || milliamps<2 || milliamps>16 || (milliamps&1))
        return 0xffffffffu;
    return pull | function<<2 | ((milliamps/2u)-1u)<<6 | (output?1u<<9:0u);
}

struct AmethystPinPlan { unsigned pin; uint32_t ctl; };
// SPI mux per the board DTS active states. Applied only after the internal
// loopback (which requires the pins to stay in GPIO mode) has passed.
static const struct AmethystPinPlan amethyst_spi_pins[4] = {
    {AmethystPinMiso, (uint32_t)(AmethystQup0Se0Function<<2 | 2u<<6)},  // 6mA
    {AmethystPinMosi, (uint32_t)(AmethystQup0Se0Function<<2 | 1u<<6)},  // 4mA
    {AmethystPinClk,  (uint32_t)(AmethystQup0Se0Function<<2 | 1u<<6)},  // 4mA
    {AmethystPinCs,   (uint32_t)(AmethystQup0Se0Function<<2 | 2u<<6)},  // 6mA
};
static const uint32_t amethyst_reset_ctl = (3u<<6) | (1u<<9);  // GPIO, 8mA, output
static const uint32_t amethyst_irq_ctl = 0;                      // GPIO input, no bias

// VRM commands for one rail: high-power mode, voltage, enable.
static inline void amethyst_rail_commands(uint32_t address,uint32_t millivolts,
    uint32_t addresses[3],uint32_t values[3]) {
    addresses[0]=address+8; values[0]=7;
    addresses[1]=address;   values[1]=millivolts;
    addresses[2]=address+4; values[2]=1;
}
