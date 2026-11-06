#include "AmethystButtons.h"
#include <assert.h>
int main(void) {
    struct AmethystButtonsFilter f={0};uint8_t report=255;
    assert(amethyst_buttons_mask(0,1)==0);
    assert(amethyst_buttons_mask(0x80,1)==2);
    assert(amethyst_buttons_mask(0x40,1)==8);
    assert(amethyst_buttons_mask(0,0)==4);
    assert(amethyst_buttons_mask(0xc0,0)==14);
    assert(!amethyst_buttons_step(&f,1,2,&report) && !report);
    assert(!amethyst_buttons_step(&f,1,0,&report)); // bounce
    assert(!amethyst_buttons_step(&f,1,2,&report));
    assert(amethyst_buttons_step(&f,1,2,&report) && report==2);
    assert(!amethyst_buttons_step(&f,1,2,&report)); // held, not repeated
    assert(amethyst_buttons_step(&f,0,14,&report) && report==0);
    assert(!amethyst_buttons_step(&f,1,1,&report) && report==0); // no Home
    assert(!amethyst_buttons_step(&f,1,4,&report));
    assert(amethyst_buttons_step(&f,1,4,&report) && report==4);
    assert(!amethyst_buttons_step(&f,1,0,&report));
    assert(amethyst_buttons_step(&f,1,0,&report) && !report);
    return 0;
}
