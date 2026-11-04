// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <assert.h>
#include <stdio.h>
#include "AmethystTouchPower.h"
int main(void) {
    // Encodings must equal the DTS active states through the generic encoder.
    const unsigned ma[4]={6,4,4,6};
    for(unsigned i=0;i<4;++i) {
        assert(amethyst_spi_pins[i].pin==i);
        assert(amethyst_spi_pins[i].ctl==amethyst_tlmm_ctl(1,ma[i],0,0));
    }
    assert(amethyst_reset_ctl==amethyst_tlmm_ctl(0,8,0,1));
    assert(amethyst_irq_ctl==0);
    // Asteroids' proven SPI value (function 1, 6mA, no pull) for comparison.
    assert(amethyst_tlmm_ctl(1,6,0,0)==((1u<<2)|(2u<<6)));
    assert(amethyst_tlmm_ctl(1,5,0,0)==0xffffffffu);
    assert(amethyst_tlmm_ctl(1,18,0,0)==0xffffffffu);
    uint32_t a[3],v[3];
    amethyst_rail_commands(0x40000,AmethystAvddMv,a,v);
    assert(a[0]==0x40008 && v[0]==7 && a[1]==0x40000 && v[1]==3224 && a[2]==0x40004 && v[2]==1);
    assert(AmethystIovddMv==1800 && AmethystAvddMv<=3300);
    puts("check-touch-power: ok");
    return 0;
}
