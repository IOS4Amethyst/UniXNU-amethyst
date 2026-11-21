// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystTouchLoopbackStage.h"
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <string.h>
#include "AmethystTouchClock.h"
#include "AmethystTouchDfs.h"
#include "AmethystTouchDma.h"
#include "AmethystTouchRoute.h"

extern "C" {
void snapshotLog(const char *format,...);
#include "GeniSpi.h"
#include "AmethystTouchLoopback.h"
int amethyst_touch_dma_prepare(uint8_t **,uint8_t **,uint64_t *);
int amethyst_touch_dma_bind(struct geni_spi *);
int amethyst_touch_dma_claim(void);
int amethyst_touch_dma_idle(void);
int amethyst_touch_dma_visibility_probe(void);
int amethyst_touch_dma_state(void);
int amethyst_touch_dma_refusal_reason(void);
int amethyst_touch_dma_prepare_count(void);
int amethyst_touch_goodix_stage(struct geni_spi *spi);
}

namespace {
constexpr uint32_t kSe0=0xa80000;
constexpr uint32_t kQupWrapper=0xac0000;
constexpr uint32_t kStatusPage=0x123000;
constexpr uint32_t kAlternativeStatusPage=0x118000;
constexpr uint32_t kQup0Vote=0x152008;
constexpr uint64_t kSmmu=0x15000000;
constexpr unsigned kSmmuBytes=0xd0000;
constexpr unsigned kMaxVoteWait=2000;

uint32_t load32(uintptr_t address) {
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(address) : "memory");
    return value;
}
void store32(uintptr_t address,uint32_t value) {
    __asm__ volatile("str %w0, [%1]\n\tdsb sy" :: "r"(value),"r"(address) : "memory");
}
IOMemoryMap *mapMemory(uint64_t address,uint64_t bytes,IOOptionBits options) {
    IODeviceMemory *memory=IODeviceMemory::withRange(address,bytes);
    if(!memory)return nullptr;
    IOMemoryMap *result=memory->map(options);
    memory->release();
    return result;
}
void seDelay(void *,unsigned microseconds) {
    IODelay(microseconds);
}
// Only the caller's own local structures are handed to the engine callbacks.
struct Se {
    uintptr_t base;
    static uint32_t read(void *context,uint32_t offset) {
        return load32(static_cast<Se *>(context)->base+offset);
    }
    static void write(void *context,uint32_t offset,uint32_t value) {
        store32(static_cast<Se *>(context)->base+offset,value);
    }
};
struct Wrapper {
    uintptr_t base;
    static uint32_t read(void *context,uint32_t offset) {
        return load32(static_cast<Wrapper *>(context)->base+offset);
    }
    static void write(void *,uint32_t,uint32_t) {}
};
// Additive vote left in place; a failed vote never clears a shared clock.
bool vote(uint32_t &before,uint32_t &after) {
    IOMemoryMap *registers=mapMemory(kQup0Vote&~4095u,4096,kIOMapInhibitCache);
    if(!registers)return false;
    uintptr_t base=registers->getVirtualAddress();
    bool valid=base && registers->getLength()>=4096;
    if(valid) {
        before=load32(base+(kQup0Vote&4095));
        uint32_t value=amethyst_qup0_vote(before);
        store32(base+(kQup0Vote&4095),value);
        after=load32(base+(kQup0Vote&4095));
        valid=(after&AmethystQup0VoteMask)==AmethystQup0VoteMask;
    }
    registers->release();
    return valid;
}
// Reads the divider back so the log records what the engine clocks, not only
// what was requested.
extern "C" { uint32_t amethyst_touch_source_hz; }
bool qualifyClock(struct geni_spi *spi,uint32_t sourceHz,uint32_t &sclkHz,uint32_t &divider) {
    uint32_t version=spi->wrapper.rd(spi->wrapper.ctx,QUPV3_HW_VER_REG);
    snapshotLog("AmethystTouch: clock wrapper=%08x oversampling=%u source=%u\n",
        version,spi->oversampling,sourceHz);
    if(version==0xffffffffu || !sourceHz)return false;
    int status=geni_spi_set_clock(spi,960000,sourceHz,0);
    snapshotLog("AmethystTouch: clock setup rc=%d\n",status);
    if(status)return false;
    uint32_t cfg=spi->se.rd(spi->se.ctx,GENI_SER_M_CLK_CFG);
    uint32_t selector=spi->se.rd(spi->se.ctx,SE_GENI_CLK_SEL);
    uint32_t cpha=spi->se.rd(spi->se.ctx,SE_SPI_CPHA);
    uint32_t cpol=spi->se.rd(spi->se.ctx,SE_SPI_CPOL);
    snapshotLog("AmethystTouch: clock cfg=%08x sel=%08x cpha=%08x cpol=%08x\n",
        cfg,selector,cpha,cpol);
    divider=(cfg>>GENI_CLK_DIV_SHIFT)&0xfffu;
    if(!(cfg&GENI_SER_CLK_EN) || !divider || selector!=0 || cpha || cpol)
        return false;
    sclkHz=sourceHz/(divider*spi->oversampling);
    return true;
}
}
extern "C" void amethyst_touch_loopback_stage(void);
// Opt-in DMA-only internal loopback. Installs the verified stream route, runs a
// single 16-byte loopback and returns the buffers to prepared only when the
// engine is proven idle. Touch reset, regulators and chip commands are not used.
extern "C" void amethyst_touch_loopback_stage(void) {
    unsigned enabled=0;
    if(!PE_parse_boot_argn("amethyst-touch-loopback",&enabled,sizeof enabled) ||
        enabled!=1)return;
    static bool started=false;
    if(__atomic_exchange_n(&started,true,__ATOMIC_ACQ_REL))return;
    uint32_t before=0,after=0;
    if(!vote(before,after)) {
        snapshotLog("AmethystTouch: loopback stopped: clock vote before=%08x after=%08x\n",before,after);
        return;
    }
    IOMemoryMap *maps[2]={mapMemory(kStatusPage,4096,0),mapMemory(kAlternativeStatusPage,4096,0)};
    if(!maps[0] || !maps[1] || !maps[0]->getVirtualAddress() ||
        !maps[1]->getVirtualAddress()) {
        snapshotLog("AmethystTouch: loopback stopped: status mapping failed\n");
        if(maps[0])maps[0]->release();
        if(maps[1])maps[1]->release();
        return;
    }
    const uint32_t offsets[5]={0x23018,0x23008,0x23000,0x23004,0x18004};
    unsigned attempt=0;
    for(;attempt<kMaxVoteWait;++attempt) {
        uint32_t clocks[5];
        for(unsigned i=0;i<5;++i)
            clocks[i]=load32((i==4?maps[1]:maps[0])->getVirtualAddress()+
                (offsets[i]&4095));
        if(amethyst_qup0_running(clocks))break;
        IODelay(10);
    }
    // SE0 RCG DFS level 0 (selector 0) gives the source rate actually programmed.
    const uintptr_t rcg=maps[1]->getVirtualAddress()+(AmethystGccS0Rcg&4095);
    const uint32_t dfsr=load32(rcg+AmethystDfsrOffset);
    const uint32_t perf0=load32(rcg+AmethystDfsPerfOffset);
    const uint32_t m0=load32(rcg+AmethystDfsMOffset);
    const uint32_t n0=load32(rcg+AmethystDfsNOffset);
    const uint32_t sourceHz=amethyst_dfs_level0_hz(dfsr,perf0,m0,n0);
    maps[0]->release();maps[1]->release();
    snapshotLog("AmethystTouch: dfs dfsr=%08x l0 cfg=%08x m=%08x n=%08x hz=%u\n",
        dfsr,perf0,m0,n0,sourceHz);
    snapshotLog("AmethystTouch: loopback vote %08x->%08x wait=%u max=%u\n",
        before,after,attempt,kMaxVoteWait);
    if(attempt==kMaxVoteWait)return;
    IOMemoryMap *se=mapMemory(kSe0,0x4000,kIOMapInhibitCache);
    IOMemoryMap *wrapper=mapMemory(kQupWrapper,4096,kIOMapInhibitCache);
    IOMemoryMap *smmu=mapMemory(kSmmu,kSmmuBytes,kIOMapInhibitCache);
    if(!se || !wrapper || !smmu || !se->getVirtualAddress() ||
        !wrapper->getVirtualAddress() || !smmu->getVirtualAddress()) {
        snapshotLog("AmethystTouch: loopback stopped: engine/SMMU mapping failed\n");
        if(se)se->release();
        if(wrapper)wrapper->release();
        if(smmu)smmu->release();
        return;
    }
    // Static: the touch event poller (amethyst-touch-events) keeps using the engine after this returns.
    static Se seContext;seContext.base=se->getVirtualAddress();
    static Se smmuContext;smmuContext.base=smmu->getVirtualAddress();
    static Wrapper wrapperContext;wrapperContext.base=wrapper->getVirtualAddress();
    if(Se::read(&seContext,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE) {
        snapshotLog("AmethystTouch: loopback skipped: engine already active\n");
        se->release();wrapper->release();smmu->release();
        return;
    }
    static struct geni_spi spi;memset(&spi,0,sizeof spi);
    spi.se.ctx=&seContext;spi.se.rd=Se::read;spi.se.wr=Se::write;spi.se.delay_us=seDelay;
    spi.wrapper.ctx=&wrapperContext;spi.wrapper.rd=Wrapper::read;spi.wrapper.wr=Wrapper::write;
    spi.poll_limit=200000;spi.poll_only=1;
    // Read-only identity first: the survey values must still hold before any write.
    int status=geni_spi_read_info(&spi);
    snapshotLog("AmethystTouch: loopback identity info=%d proto=%u fifo=%d fw=%08x hw=%08x\n",
        status,spi.info.protocol,spi.info.fifo_disabled,spi.info.fw_revision,spi.info.hw_param0);
    if(status || spi.info.protocol!=GENI_PROTO_SPI || spi.info.fifo_disabled!=1) {
        snapshotLog("AmethystTouch: loopback stopped: identity mismatch\n");
        return;
    }
    uint8_t *tx=nullptr,*rx=nullptr;uint64_t dma=0;
    status=amethyst_touch_dma_prepare(&tx,&rx,&dma);
    if(!status)status=amethyst_touch_dma_bind(&spi);
    snapshotLog("AmethystTouch: loopback DMA prepare/bind=%d tx=%llx state=%d calls=%d refusal=%d\n",
        status,(unsigned long long)dma,amethyst_touch_dma_state(),
        amethyst_touch_dma_prepare_count(),amethyst_touch_dma_refusal_reason());
    if(status) {
        snapshotLog("AmethystTouch: loopback stopped: buffers unavailable\n");
        return;
    }
    AmethystTouchRoute plan=amethyst_touch_route(Se::read,&smmuContext,dma,AmethystTouchDmaBytes);
    snapshotLog("AmethystTouch: loopback route valid=%d slot=%u bank=%u reuse=%d\n",
        plan.valid,plan.slot,plan.bank,plan.reuse);
    if(!plan.valid) {
        snapshotLog("AmethystTouch: loopback route refused; no mapping written\n");
        return;
    }
    if(amethyst_touch_dma_claim()) {
        snapshotLog("AmethystTouch: loopback stopped: buffers already in use\n");
        return;
    }
    status=amethyst_touch_dma_visibility_probe();
    snapshotLog("AmethystTouch: loopback cache visibility=%d\n",status);
    if(status) {
        snapshotLog("AmethystTouch: loopback skipped: DMA memory not coherently visible\n");
        return;
    }
    status=amethyst_touch_route_commit(Se::read,Se::write,&smmuContext,&plan,dma,AmethystTouchDmaBytes);
    snapshotLog("AmethystTouch: loopback route commit=%d\n",status);
    if(status) {
        snapshotLog("AmethystTouch: loopback stopped: route not published; no DMA\n");
        return;
    }
    spi.use_dma=1;
    status=geni_spi_init(&spi);
    snapshotLog("AmethystTouch: loopback init rc=%d\n",status);
    uint32_t divider=0,sclkHz=0;
    if(!status && !qualifyClock(&spi,sourceHz,sclkHz,divider)) {
        snapshotLog("AmethystTouch: loopback stopped: clock source unqualified; buffers quarantined\n");
        return;
    }
    if(!status)amethyst_touch_source_hz=sourceHz;   // the Goodix stage raises the bus clock from it
    if(!status)snapshotLog("AmethystTouch: loopback clock div=%u sclk=%u request=960000\n",divider,sclkHz);
    if(!status)status=geni_spi_setup(&spi,0,0);
    AmethystLoopbackResult result;memset(&result,0,sizeof result);
    if(!status)status=amethyst_touch_loopback(&spi,&result);
    snapshotLog("AmethystTouch: loopback rc=%d pre=%d idle=%d txb=%u rxb=%u\n",
        status,result.transferStatus,result.idle,result.txBytes,result.rxBytes);
    snapshotLog("AmethystTouch: loopback match=%d st=%08x data=%02x%02x%02x%02x\n",
        result.matched,result.status,result.received[0],result.received[1],
        result.received[2],result.received[3]);
    if(result.idle && !result.transferStatus) {
        snapshotLog("AmethystTouch: loopback buffers released=%d\n",amethyst_touch_dma_idle());
        // Opt-in controller bring-up only after a passing loopback.
        if(result.matched && amethyst_touch_goodix_stage(&spi))
            return; // Engine uncertain: retain mappings and buffers.
    } else {
        snapshotLog("AmethystTouch: loopback buffers quarantined; no retry this boot\n");
        return; // Retain mappings as well as buffers on an uncertain stop.
    }
    se->release();wrapper->release();smmu->release();
}
