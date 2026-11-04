// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>
// Consumer report bits: power, volume increment, volume decrement. Bit0
// (Home in the legacy combined descriptor) is deliberately never generated.
static inline uint8_t amethyst_buttons_mask(uint8_t pon,uint8_t gpio) {
    return ((pon&0x80)?2:0)|((gpio&1)?0:4)|((pon&0x40)?8:0);
}
// ponytail: two samples at 10ms intervals; use interrupts if short taps require it.
struct AmethystButtonsFilter { uint8_t previous,stable; };
static inline int amethyst_buttons_step(struct AmethystButtonsFilter *f,
    int transport_ok,uint8_t sample,uint8_t *report) {
    if(!f || !report)return 0;
    if(!transport_ok) {
        // Release held keys on transport loss, never synthesize a press.
        f->previous=0;*report=0;
        int changed=f->stable!=0;f->stable=0;return changed;
    }
    sample&=14;
    int changed=sample==f->previous && sample!=f->stable;
    if(changed)f->stable=sample;
    f->previous=sample;*report=f->stable;return changed;
}
