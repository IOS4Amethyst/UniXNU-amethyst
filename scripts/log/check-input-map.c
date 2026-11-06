// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystInputMap.h"
#include <assert.h>
int main(void) {
    assert(amethyst_consumer_usage(116)==0x30);
    assert(amethyst_consumer_usage(115)==0xe9);
    assert(amethyst_consumer_usage(114)==0xea);
    assert(!amethyst_consumer_usage(102)); // Home is not a side key.
    uint32_t out=123;
    assert(amethyst_touch_coordinate(0,1220,30480,&out) && !out);
    assert(amethyst_touch_coordinate(1219,1220,30480,&out) && out==30480);
    assert(amethyst_touch_coordinate(2711,2712,20320,&out) && out==20320);
    unsigned previous=0;
    for(unsigned x=0;x<1220;++x){assert(amethyst_touch_coordinate(x,1220,30480,&out));assert(out>=previous && out<=30480);previous=out;}
    out=123;assert(!amethyst_touch_coordinate(1220,1220,30480,&out) && out==123);
    assert(!amethyst_touch_coordinate(0,1,30480,&out));
    assert(!amethyst_touch_coordinate(0,1220,30480,0));
    return 0;
}
