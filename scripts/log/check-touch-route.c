// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystTouchRoute.h"
#include <assert.h>
#include <string.h>
static uint32_t registers[0xd0000/4];
static unsigned writes,failWrite;
static void write_reg(void *context,unsigned offset,uint32_t value) {
    (void)context;
    // Never touch bank state or UFS slot113. Only the selected empty slot0.
    assert(offset==(writes==0?0xc00u:0x800u));
    ++writes;
    if(writes!=failWrite)registers[offset/4]=value;
}
static uint32_t read_reg(void *context,unsigned offset) {
    (void)context;assert(offset<sizeof registers && !(offset&3));return registers[offset/4];
}
static void fixture(void) {
    writes=failWrite=0;
    memset(registers,0,sizeof registers);
    registers[0x20/4]=0x4c017e72;registers[0x24/4]=0x60000048;registers[0x28/4]=0x5111;
    registers[(0x800+113*4)/4]=0x80000060;registers[(0xc00+113*4)/4]=71;
    registers[(0x1000+71*4)/4]=0x10000;
    registers[(0x1800+71*4)/4]=1;
}
int main(void) {
    // Matching follows the surveyed compare, not a plain equality.
    assert(amethyst_smmu_matches(0x80000060,0x60));
    assert(!amethyst_smmu_matches(0x80000060,0x563));
    assert(amethyst_smmu_matches(0x80000563,0x563));
    assert(!amethyst_smmu_matches(0x80000563,0x576));
    assert(amethyst_smmu_matches(0x80010563,0x563));   // bit0 mask ignores sid bit0
    assert(!amethyst_smmu_matches(0x80010560,0x563));
    // Fresh install into the first free slot, then the published entry is reused.
    fixture();struct AmethystTouchRoute p=amethyst_touch_route(read_reg,0,0x90000000,8192);
    assert(p.valid && p.bank==71 && p.slot==0 && !p.reuse);
    assert(!amethyst_touch_route_commit(read_reg,write_reg,0,&p,0x90000000,8192));
    assert(writes==2 && registers[(0x800+113*4)/4]==0x80000060);
    p=amethyst_touch_route(read_reg,0,0x90000000,8192);
    assert(p.valid && p.reuse && !p.slot);
    assert(!amethyst_touch_route_commit(read_reg,write_reg,0,&p,0x90000000,8192) && writes==2);
    // A stale slot value or a non-bypass bank aborts before any write.
    fixture();p=amethyst_touch_route(read_reg,0,0x90000000,8192);
    registers[0x800/4]=0x80000777;
    assert(amethyst_touch_route_commit(read_reg,write_reg,0,&p,0x90000000,8192)==-22 && !writes);
    fixture();p=amethyst_touch_route(read_reg,0,0x90000000,8192);
    registers[(0x1000+71*4)/4]=0;
    assert(amethyst_touch_route_commit(read_reg,write_reg,0,&p,0x90000000,8192)==-22 && !writes);
    fixture();p=amethyst_touch_route(read_reg,0,0x90000000,8192);failWrite=1;
    assert(amethyst_touch_route_commit(read_reg,write_reg,0,&p,0x90000000,8192)==-5 && writes==1);
    assert(!registers[0x800/4]);
    fixture();p=amethyst_touch_route(read_reg,0,0x90000000,8192);failWrite=2;
    assert(amethyst_touch_route_commit(read_reg,write_reg,0,&p,0x90000000,8192)==-5 && writes==2);
    // An existing touch entry must share the UFS bank to authorize reuse.
    fixture();registers[0x800/4]=0x80000563;registers[0xc00/4]=71;
    p=amethyst_touch_route(read_reg,0,0x90000000,8192);assert(p.valid && p.reuse && !p.slot);
    registers[0xc00/4]=70;assert(!amethyst_touch_route(read_reg,0,0x90000000,8192).valid);
    // A masked SID0x060 entry is not an attributable UFS match, so planning stops
    // rather than silently reusing a bank it cannot prove owns the stream.
    fixture();registers[(0x800+1*4)/4]=0x80010060;
    assert(!amethyst_touch_route(read_reg,0,0x90000000,8192).valid);
    // Two enabled UFS matches are ambiguous.
    fixture();registers[(0x800+1*4)/4]=0x80000060;
    assert(!amethyst_touch_route(read_reg,0,0x90000000,8192).valid);
    fixture();registers[((128+71)*4096)/4]=1;
    assert(!amethyst_touch_route(read_reg,0,0x90000000,8192).valid);
    fixture();assert(!amethyst_touch_route(read_reg,0,UINT64_MAX,8192).valid);
    assert(!amethyst_touch_route(read_reg,0,0x90000000,0).valid);
    // Exhaustion with every group enabled and no free slot.
    for(unsigned i=0;i<114;++i)registers[(0x800+4*i)/4]=0x80001000+i;
    registers[(0x800+113*4)/4]=0x80000060;
    assert(!amethyst_touch_route(read_reg,0,0x90000000,8192).valid);
    return 0;
}
