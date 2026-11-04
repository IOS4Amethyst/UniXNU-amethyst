// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <assert.h>
#include <stdio.h>
#include "AmethystTouchDfs.h"
int main(void) {
    // TCXO, no divider: what Asteroids observed at level 0.
    assert(amethyst_dfs_level0_hz(1,0x000,0,0)==19200000u);
    // DFS disabled: level registers are not authoritative.
    assert(amethyst_dfs_level0_hz(0,0x000,0,0)==0);
    // Half divider 3 => /2.
    assert(amethyst_dfs_level0_hz(1,0x003,0,0)==9600000u);
    assert(amethyst_dfs_level0_hz(1,0x103,0,0)==300000000u);
    // GPLL0 even, MND 1/4 (N register stores ~(n-m)).
    assert(amethyst_dfs_level0_hz(1,0x2600,1,~3u)==75000000u);
    // Unknown source, bad M/N refuse.
    assert(amethyst_dfs_level0_hz(1,0x200,0,0)==0);
    assert(amethyst_dfs_level0_hz(1,0x2000,0,~3u)==0);
    puts("check-touch-dfs: ok");
    return 0;
}
