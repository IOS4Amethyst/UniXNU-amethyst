// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>

// Never initializes, disables or rewrites the UFS-owned context bank.
struct AmethystTouchRoute { unsigned slot, bank; int reuse, valid; };
typedef uint32_t (*AmethystSmmuRead)(void *,unsigned);
// Callback must complete the MMIO write/barrier before returning.
typedef void (*AmethystSmmuWrite)(void *,unsigned,uint32_t);
// Amethyst SMR layout, verified against the read-only survey: bit31 enables the
// entry, bits30:16 hold a byte mask over the low 16 bits and bits15:0 hold the
// stream ID. The observed UFS entry 0x80000060 is SID0x60 with mask0 and yet
// matched only 0x60, because matching compares (smr^stream)&0xffff&~mask: the
// mask never widens the compare beyond the low 16 bits.
static inline uint32_t amethyst_smmu_sid(uint32_t smr) {
    return smr & 0xffffu;
}
static inline unsigned amethyst_smmu_mask(uint32_t smr) {
    return (unsigned)((smr >> 16) & 0x7fffu);
}
static inline int amethyst_smmu_matches(uint32_t smr,uint32_t stream) {
    return ((smr^stream)&0xffffu&~amethyst_smmu_mask(smr))==0;
}
static inline unsigned amethyst_smmu_bits(unsigned code) {
    const unsigned widths[]={32,36,40,42,44,48};
    return code<6?widths[code]:0;
}
static inline struct AmethystTouchRoute amethyst_touch_route(
    AmethystSmmuRead read,void *context,uint64_t dma,uint64_t bytes) {
    struct AmethystTouchRoute plan={~0u,~0u,0,0};
    if(!read || !bytes)return plan;
    uint32_t id0=read(context,0x20),id1=read(context,0x24),id2=read(context,0x28);
    unsigned count=id0&255,banks=id1&255,pages=1u<<(((id1>>28)&7)+1);
    if(!(id0&(1u<<27)) || !(id0&(1u<<30)) || !(id2&(1u<<12)) ||
        (id1>>31) || !count || count>128 || !banks || banks>128 ||
        (pages+banks)*4096u>0xd0000u)return plan;
    for(unsigned shift=0;shift<=8;shift+=4) {
        unsigned bits=amethyst_smmu_bits((id2>>shift)&15);
        if(!bits || dma>=(1ull<<bits) || bytes>(1ull<<bits)-dma)return plan;
    }
    unsigned ufsMatches=0,touchMatches=0,touchSlot=~0u,touchRoute=0;
    for(unsigned i=0;i<count;++i) {
        uint32_t smr=read(context,0x800+4*i);
        if(!(smr&0x80000000u)){if(plan.slot==~0u)plan.slot=i;continue;}
        // UFS must be an exact single match, and a masked match cannot be
        // attributed to one owner, so only an unmasked SID0x60 entry is accepted.
        if(amethyst_smmu_matches(smr,0x60)) {
            if(amethyst_smmu_mask(smr) || ++ufsMatches!=1)return plan;
            uint32_t route=read(context,0xc00+4*i);
            if(((route>>16)&3)!=0)return plan;
            plan.bank=route&255;
        }
        if(amethyst_smmu_matches(smr,0x563)) {
            // Commit only accepts the exact stream entry; reject wildcard
            // reuse during planning, before any controller initialization.
            if(smr!=0x80000563u)return plan;
            if(++touchMatches!=1)return plan;
            touchSlot=i;touchRoute=read(context,0xc00+4*i);
        }
    }
    if(ufsMatches!=1 || plan.bank>=banks ||
        read(context,(pages+plan.bank)*4096u)!=0 ||
        read(context,0x1000+4*plan.bank)!=0x10000 ||
        read(context,0x1800+4*plan.bank)!=1)return plan;
    if(touchMatches) {
        if(touchRoute!=plan.bank)return plan;
        plan.slot=touchSlot;plan.reuse=1;
    }
    if(plan.slot==~0u)return plan;
    plan.valid=1;return plan;
}
// Caller must serialize the complete plan/commit with all stream-table writers,
// own an idle SE0 and retain the DMA allocation. Revalidation is not a lock.
// Returns0 on verified success, -22 on invalid/stale plan, -5 on readback error.
// A readback error may leave a partial route: do not DMA or blindly roll back
// a shared table. Keep the controller idle and inspect the recorded state.
static inline int amethyst_touch_route_commit(AmethystSmmuRead read,
    AmethystSmmuWrite write,void *context,const struct AmethystTouchRoute *plan,
    uint64_t dma,uint64_t bytes) {
    if(!read || !write || !plan || !plan->valid)return -22;
    struct AmethystTouchRoute current=amethyst_touch_route(read,context,dma,bytes);
    if(!current.valid || current.slot!=plan->slot || current.bank!=plan->bank ||
        current.reuse!=plan->reuse)return -22;
    unsigned smr=0x800+4*plan->slot,s2cr=0xc00+4*plan->slot;
    if(!plan->reuse) {
        if(read(context,smr)&0x80000000u)return -22;
        write(context,s2cr,plan->bank);
        if(read(context,s2cr)!=plan->bank)return -5;
        // Publish a valid stream match only after its route reads back.
        write(context,smr,0x80000563u);
    }
    if(read(context,smr)!=0x80000563u || read(context,s2cr)!=plan->bank)return -5;
    current=amethyst_touch_route(read,context,dma,bytes);
    return current.valid && current.reuse && current.slot==plan->slot &&
        current.bank==plan->bank?0:-5;
}
