// SPDX-License-Identifier: BSD-2-Clause-Patent
// QUP0 SE0 source clock from the GCC RCG DFS level registers, so the SPI
// divider is computed from the programmed rate instead of an assumed TCXO.
// Offsets and decoding follow rootstockos/UniXNU-asteroids
// package/AsteroidsTouchProbe.cpp (same SM7635 gcc-volcano block) and Linux
// clk-rcg2 calc_rate: source 10:8, mode 13:12, half divider 4:0, N inverted.
#pragma once
#include <stdint.h>

enum {
    AmethystGccS0Rcg = 0x118010,       // CMD; CFG +4; DFSR +0x14
    AmethystDfsrOffset = 0x14,
    AmethystDfsPerfOffset = 0x1c,      // per level +4
    AmethystDfsMOffset = 0x5c,
    AmethystDfsNOffset = 0x9c,
    AmethystDfsEnable = 1u,
};

// Returns 0 for any source whose parent rate is not known from the reference.
static inline uint64_t amethyst_dfs_level_hz(uint32_t cfg,uint32_t mreg,uint32_t nreg) {
    const unsigned source=(cfg>>8)&7u,mode=(cfg>>12)&3u,half=cfg&0x1fu;
    uint64_t rate=source==0?19200000ull:source==1?600000000ull:
        source==6?300000000ull:0;
    if(!rate)return 0;
    if(half) {
        rate*=2;
        rate/=half+1u;
    }
    if(mode) {
        const uint32_t m=mreg&0xffffu,n=((~nreg)&0xffffu)+m;
        if(!m || !n || m>n)return 0;
        rate=rate*m/n;
    }
    return rate;
}

// Level 0 is what SE_GENI_CLK_SEL=0 selects; require DFS mode so the level
// registers are the ones actually feeding the engine.
static inline uint32_t amethyst_dfs_level0_hz(uint32_t dfsr,uint32_t cfg,
    uint32_t mreg,uint32_t nreg) {
    if(!(dfsr&AmethystDfsEnable))return 0;
    uint64_t rate=amethyst_dfs_level_hz(cfg,mreg,nreg);
    return rate>0xffffffffull?0:(uint32_t)rate;
}
