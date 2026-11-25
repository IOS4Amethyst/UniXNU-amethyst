// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>
enum { AmethystTouchDmaHalf=4096, AmethystTouchDmaBytes=8192 };
// 0 free, 1 prepared (nothing submitted), 2 submitted-or-uncertain (quarantine).
// Only a proven-idle transaction may leave quarantine.
static inline int amethyst_touch_dma_acquire(int *state) {
    int expected=0;
    return state && __atomic_compare_exchange_n(state,&expected,1,0,
        __ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
}
// Called before programming any register that can start DMA. A failed acquirer
// must not touch the engine or the buffers.
static inline int amethyst_touch_dma_claim(int *state) {
    int expected=1;
    return state && __atomic_compare_exchange_n(state,&expected,2,0,
        __ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
}
// Sequential stages only: a completed, proven-idle transaction returns to1.
static inline int amethyst_touch_dma_idle(int *state) {
    int expected=2;
    return state && __atomic_compare_exchange_n(state,&expected,1,0,
        __ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE);
}
static inline int amethyst_touch_dma_range(uintptr_t base,uintptr_t pointer,
    uint64_t bytes) {
    return base && pointer>=base && pointer-base<AmethystTouchDmaHalf &&
        bytes && bytes<=AmethystTouchDmaHalf-(pointer-base);
}
// ID2=0x5111 observed on Amethyst: the narrowest DMA address width is36 bits.
static inline int amethyst_touch_dma_extent(uint64_t physical,uint64_t contiguous,
    uintptr_t virtualAddress) {
    return physical && virtualAddress && !(physical&4095) && !(virtualAddress&4095) &&
        contiguous>=AmethystTouchDmaBytes &&
        physical<(1ull<<36) && AmethystTouchDmaBytes<=(1ull<<36)-physical &&
        virtualAddress<=UINTPTR_MAX-AmethystTouchDmaBytes;
}
