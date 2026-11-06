// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystTouchDma.h"
#include <assert.h>
int main(void) {
    assert(amethyst_touch_dma_range(0x10000,0x10000,4096));
    assert(amethyst_touch_dma_range(0x10000,0x10fff,1));
    assert(!amethyst_touch_dma_range(0x10000,0x10fff,2));
    assert(!amethyst_touch_dma_range(0x10000,0xffff,1));
    assert(!amethyst_touch_dma_range(0x10000,0x11000,1));
    assert(!amethyst_touch_dma_range(0x10000,0x10000,0));
    assert(amethyst_touch_dma_extent(0x90000000,8192,0x100000));
    assert(!amethyst_touch_dma_extent(0,8192,0x100000));
    assert(!amethyst_touch_dma_extent(0x90000001,8192,0x100000));
    assert(!amethyst_touch_dma_extent(0x90000000,8191,0x100000));
    assert(!amethyst_touch_dma_extent(0x90000000,8192,0x100001));
    assert(amethyst_touch_dma_extent((1ull<<36)-8192,8192,0x100000));
    assert(!amethyst_touch_dma_extent((1ull<<36)-4096,8192,0x100000));
    assert(!amethyst_touch_dma_extent(0x90000000,8192,UINTPTR_MAX&~(uintptr_t)4095));
    // Ownership: one acquirer at a time; a claim quarantines; only a proven-idle
    // transaction returns the buffers to prepared, and a claim frees nothing.
    int state=0;
    assert(!amethyst_touch_dma_claim(&state) && state==0);
    assert(amethyst_touch_dma_acquire(&state) && state==1);
    assert(!amethyst_touch_dma_acquire(&state) && state==1);
    assert(amethyst_touch_dma_claim(&state));                // submitted or uncertain
    assert(!amethyst_touch_dma_claim(&state) && state==2);   // never double-claims
    assert(!amethyst_touch_dma_acquire(&state));            // quarantined is terminal
    assert(amethyst_touch_dma_idle(&state) && state==1);
    assert(!amethyst_touch_dma_idle(&state));               // idle only from2
    assert(!amethyst_touch_dma_acquire(0) && !amethyst_touch_dma_claim(0));
    assert(!amethyst_touch_dma_idle(0));
    return 0;
}
