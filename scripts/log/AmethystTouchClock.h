// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>
enum { AmethystQup0VoteMask = 0x1fu << 18 };
static inline uint32_t amethyst_qup0_vote(uint32_t previous) {
    return previous | AmethystQup0VoteMask;
}
// Conservative: reject CLK_OFF even on hardware-gated branches. A false
// negative skips SE access; it must never authorize an unclocked access.
static inline int amethyst_qup0_running(const uint32_t clocks[5]) {
    for(unsigned i=0;i<5;++i)if(clocks[i]&0x80000000u)return 0;
    return 1;
}
