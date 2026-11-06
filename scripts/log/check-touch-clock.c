#include "AmethystTouchClock.h"
#include <assert.h>
int main(void) {
    const uint32_t values[]={0,0xffffffff,0x48000900,0xa55aa55a};
    for(unsigned i=0;i<4;++i){
        uint32_t after=amethyst_qup0_vote(values[i]);
        assert((after&AmethystQup0VoteMask)==AmethystQup0VoteMask);
        assert((after&~AmethystQup0VoteMask)==(values[i]&~AmethystQup0VoteMask));
    }
    uint32_t clocks[5]={0};assert(amethyst_qup0_running(clocks));
    for(unsigned i=0;i<5;++i){clocks[i]=0x80004000;assert(!amethyst_qup0_running(clocks));clocks[i]=0;}
    return 0;
}
