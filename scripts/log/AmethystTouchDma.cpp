// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystTouchDma.h"
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOLib.h>
#include <kern/task.h>
extern "C" {
#include "GeniSpi.h"
}

namespace {
// ponytail: a single probe per boot; retain prepared memory until reboot.
// Add teardown only after hardware quiescence is proven, never on a DMA timeout.
IOBufferMemoryDescriptor *allocation;
uint8_t *address;
uint64_t physical;
int state;
uint8_t visibility=0xa5;
void maintain(void *pointer,size_t bytes,unsigned operation) {
    uintptr_t base=reinterpret_cast<uintptr_t>(address),at=reinterpret_cast<uintptr_t>(pointer);
    if(!allocation || (!amethyst_touch_dma_range(base,at,bytes) &&
        !amethyst_touch_dma_range(base+AmethystTouchDmaHalf,at,bytes)))
        panic("AmethystTouch: cache range outside retained DMA buffers");
    uint64_t ctr;__asm__ volatile("mrs %0, ctr_el0":"=r"(ctr));
    uintptr_t line=4ull<<((ctr>>16)&15);
    // Page-isolated halves prevent cache maintenance from touching other owners.
    if(line>AmethystTouchDmaHalf)panic("AmethystTouch: unsupported cache line");
    uintptr_t end=at+bytes;
    __asm__ volatile("dsb sy":::"memory");
    for(at&=~(line-1);at<end;at+=line) {
        if(operation==0)__asm__ volatile("dc cvac, %0"::"r"(at):"memory");
        else if(operation==1)__asm__ volatile("dc civac, %0"::"r"(at):"memory");
        else __asm__ volatile("dc ivac, %0"::"r"(at):"memory");
    }
    __asm__ volatile("dsb sy":::"memory");
}
void clean(void *,void *p,size_t n){maintain(p,n,0);}
void cleanInvalidate(void *,void *p,size_t n){maintain(p,n,1);}
void invalidate(void *,void *p,size_t n){maintain(p,n,2);}
}
// Refusal causes are observable: a stop must be attributable from one boot.
// 0 none, 1 unavailable ownership, 2 allocation failed, 3 invalid extent.
static int amethyst_touch_dma_refusal;
static int amethyst_touch_dma_prepare_calls;
extern "C" int amethyst_touch_dma_prepare(uint8_t **tx,uint8_t **rx,uint64_t *base);
// Caller owns serialization and calls in normal thread context. Allocation only:
// does not program SMMU/GENI registers or start a DMA operation.
extern "C" int amethyst_touch_dma_prepare(uint8_t **tx,uint8_t **rx,uint64_t *base) {
    __atomic_add_fetch(&amethyst_touch_dma_prepare_calls,1,__ATOMIC_ACQ_REL);
    if(!tx || !rx || !base)return -22;
    *tx=nullptr;*rx=nullptr;*base=0;
    __atomic_store_n(&amethyst_touch_dma_refusal,0,__ATOMIC_RELEASE);
    // Sequential preflight and loopback share one retained allocation. This is
    // not a second ownership acquisition; quarantine must never be adopted.
    if(allocation && __atomic_load_n(&state,__ATOMIC_ACQUIRE)==1) {
        *tx=address;*rx=address+AmethystTouchDmaHalf;*base=physical;
        return 0;
    }
    if(!amethyst_touch_dma_acquire(&state)) {
        __atomic_store_n(&amethyst_touch_dma_refusal,1,__ATOMIC_RELEASE);
        return -16;
    }
    if(!allocation) {
        IOBufferMemoryDescriptor *candidate=IOBufferMemoryDescriptor::inTaskWithPhysicalMask(
            kernel_task,static_cast<IOOptionBits>(kIODirectionInOut)|
            static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous),AmethystTouchDmaBytes,0xfffffff000ull);
        if(!candidate){amethyst_touch_dma_refusal=2;state=0;return -12;}
        IOReturn status=candidate->prepare();
        if(status!=kIOReturnSuccess){amethyst_touch_dma_refusal=2;candidate->release();state=0;return -12;}
        addr64_t length=0;
        uint64_t pa=candidate->getPhysicalSegment(0,&length);
        uint8_t *va=static_cast<uint8_t *>(candidate->getBytesNoCopy());
        if(!amethyst_touch_dma_extent(pa,length,reinterpret_cast<uintptr_t>(va))) {
            __atomic_store_n(&amethyst_touch_dma_refusal,3,__ATOMIC_RELEASE);
            candidate->complete();candidate->release();state=0;return -22;
        }
        // Nothing has been submitted to hardware, so initialization is safe.
        bzero(va,AmethystTouchDmaBytes);
        address=va;physical=pa;allocation=candidate;
    }
    *tx=address;*rx=address+AmethystTouchDmaHalf;*base=physical;
    return 0;
}
// The retained memory is normal cacheable kernel memory, distinct from the
// caller's stack. A stack echo would not show whether invalidated lines are
// visible after write-back and re-read.
extern "C" void *amethyst_touch_dma_visibility(void);
extern "C" void *amethyst_touch_dma_visibility(void) {
    return allocation?address+AmethystTouchDmaHalf+AmethystTouchDmaHalf/2:nullptr;
}
extern "C" int amethyst_touch_dma_bind(struct geni_spi *spi);
// Binding does not select DMA mode or submit work. Caller owns an idle engine
// and exclusive access to these buffers for the entire transaction.
extern "C" int amethyst_touch_dma_bind(struct geni_spi *spi) {
    if(!spi || !allocation)return -22;
    if(__atomic_load_n(&state,__ATOMIC_ACQUIRE)!=1)return -16;
    spi->dma.ctx=nullptr;spi->dma.clean=clean;
    spi->dma.clean_inval=cleanInvalidate;spi->dma.inval=invalidate;
    spi->dma.tx_virt=address;spi->dma.rx_virt=address+AmethystTouchDmaHalf;
    spi->dma.tx_phys=physical;spi->dma.rx_phys=physical+AmethystTouchDmaHalf;
    spi->dma.size=AmethystTouchDmaHalf;
    return 0;
}
extern "C" int amethyst_touch_dma_claim(void);
extern "C" int amethyst_touch_dma_claim(void) {
    return amethyst_touch_dma_claim(&state)?0:-16;
}
extern "C" int amethyst_touch_dma_idle(void);
extern "C" int amethyst_touch_dma_idle(void) {
    return amethyst_touch_dma_idle(&state)?0:-16;
}
extern "C" int amethyst_touch_dma_state(void);
extern "C" int amethyst_touch_dma_state(void) {
    return __atomic_load_n(&state,__ATOMIC_ACQUIRE);
}
// Exposed so the boot log can distinguish "never allocated" from "already held".
extern "C" int amethyst_touch_dma_refusal_reason(void);
extern "C" int amethyst_touch_dma_refusal_reason(void) {
    return __atomic_load_n(&amethyst_touch_dma_refusal,__ATOMIC_ACQUIRE);
}
extern "C" int amethyst_touch_dma_prepare_count(void);
extern "C" int amethyst_touch_dma_prepare_count(void) {
    return __atomic_load_n(&amethyst_touch_dma_prepare_calls,__ATOMIC_ACQUIRE);
}
extern "C" int amethyst_touch_dma_visibility_probe(void);
// Writes through clean, drops the lines, reads again and restores the original
// byte. A mismatch means this buffer cannot currently hold DMA data coherently.
extern "C" int amethyst_touch_dma_visibility_probe(void) {
    uint8_t *probe=static_cast<uint8_t *>(amethyst_touch_dma_visibility());
    if(!probe)return -22;
    if(__atomic_load_n(&state,__ATOMIC_ACQUIRE)!=2)return -16;
    volatile uint8_t *observedByte=probe;
    uint8_t original=*observedByte;
    *observedByte=visibility;
    clean(nullptr,probe,1);
    invalidate(nullptr,probe,1);
    uint8_t observed=*observedByte;
    *observedByte=original;
    clean(nullptr,probe,1);
    invalidate(nullptr,probe,1);
    return observed==visibility && *observedByte==original?0:-5;
}
