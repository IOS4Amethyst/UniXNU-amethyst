// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opt-in board snapshot. Mode2 additionally votes QUP clocks; no SPI commands.
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include "AmethystTouchClock.h"
#include "AmethystTouchDma.h"
#include "AmethystTouchRoute.h"
#include "AmethystTouchLoopbackStage.h"
#include "AmethystSpmiRead.h"
extern "C" int amethyst_touch_dma_prepare(uint8_t **,uint8_t **,uint64_t *);

extern "C" {
char amethyst_touch_snapshot_rows[50][128];   // 48: SMP signalling row, 49: HID row
unsigned amethyst_touch_snapshot_count;
}

extern "C" void snapshotLog(const char *format,...);
extern "C" void snapshotLog(const char *format,...) {
    unsigned count=__atomic_load_n(&amethyst_touch_snapshot_count,__ATOMIC_RELAXED);
    // The last 4 rows are kept for the Goodix stage: v11 lost its identity row
    // because button rows had filled the table first.
    // Rows 43-45 are kept for the Goodix stage and rows 46-47 for the live touch
    // event rows (amethyst_touch_row_set): v11 lost its identity row to button rows.
    const bool goodix=!strncmp(format,"AmethystTouch: goodix",21);
    if(count>=46 || (!goodix && count>=43))return;
    va_list arguments;va_start(arguments,format);
    vsnprintf(amethyst_touch_snapshot_rows[count],128,format,arguments);
    va_end(arguments);
    IOLog("%s",amethyst_touch_snapshot_rows[count]);
    __atomic_store_n(&amethyst_touch_snapshot_count,count+1,__ATOMIC_RELEASE);
}
// Overwrites one fixed row (46, 47 or 48) in place; BootLog copies the table every round,
// so the slots record how the row changed over time. Only the event poller calls this.
extern "C" void amethyst_touch_row_set(unsigned index,const char *format,...);
extern "C" void amethyst_touch_row_set(unsigned index,const char *format,...) {
    if(index<46 || index>=50)return;
    va_list arguments;va_start(arguments,format);
    vsnprintf(amethyst_touch_snapshot_rows[index],128,format,arguments);
    va_end(arguments);
    if(__atomic_load_n(&amethyst_touch_snapshot_count,__ATOMIC_ACQUIRE)<=index)
        __atomic_store_n(&amethyst_touch_snapshot_count,index+1,__ATOMIC_RELEASE);
}
namespace {
uint32_t observerRead(void *p,unsigned offset) {
    uint32_t value;uintptr_t address=reinterpret_cast<uintptr_t>(p)+offset;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(address) : "memory");
    return value;
}
void observerWrite(void *p,unsigned offset,uint32_t value) {
    uintptr_t address=reinterpret_cast<uintptr_t>(p)+offset;
    __asm__ volatile("str %w0, [%1]\n\tdsb sy" :: "r"(value),"r"(address) : "memory");
}
void observerDelay(void *) { IODelay(1); }
uint32_t load32(uintptr_t address) {
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(address) : "memory");
    return value;
}
IOMemoryMap *mapReadOnly(uint64_t address,uint64_t bytes) {
    IODeviceMemory *memory=IODeviceMemory::withRange(address,bytes);
    if(!memory)return nullptr;
    IOMemoryMap *map=memory->map(kIOMapAnywhere|kIOMapInhibitCache|kIOMapReadOnly);
    memory->release();
    return map;
}
bool voteClocks(uint32_t &before,uint32_t &after) {
    IODeviceMemory *memory=IODeviceMemory::withRange(0x152000,4096);
    if(!memory)return false;
    IOMemoryMap *map=memory->map(kIOMapAnywhere|kIOMapInhibitCache);
    memory->release();
    if(!map)return false;
    uintptr_t base=map->getVirtualAddress();
    bool valid=base && map->getLength()>=4096;
    if(valid) {
        before=load32(base+8);
        uint32_t value=amethyst_qup0_vote(before);
        __asm__ volatile("str %w0, [%1]\n\tdsb sy" :: "r"(value),"r"(base+8) : "memory");
        after=load32(base+8);
        valid=(after&AmethystQup0VoteMask)==AmethystQup0VoteMask;
    }
    map->release();return valid;
}
bool readRegister(uint64_t page,unsigned offset,uint32_t &value) {
    if(offset>4092 || offset%4)return false;
    IOMemoryMap *map=mapReadOnly(page,4096);
    if(!map)return false;
    uintptr_t base=map->getVirtualAddress();
    bool valid=base && map->getLength()>=4096;
    if(valid)value=load32(base+offset);
    map->release();return valid;
}
uint32_t smmuRead(void *context,unsigned offset) {
    return load32(reinterpret_cast<uintptr_t>(context)+offset);
}
void surveySmmu(void) {
    // Same apps-SMMU aperture already used by NativeUfs. Read only; no takeover.
    IOMemoryMap *map=mapReadOnly(0x15000000,0xd0000);
    if(!map){snapshotLog("AmethystTouch: SMMU map failed\n");return;}
    uintptr_t base=map->getVirtualAddress();
    if(!base || map->getLength()<0xd0000){map->release();return;}
    uint32_t id0=load32(base+0x20),id1=load32(base+0x24),id2=load32(base+0x28);
    snapshotLog("AmethystTouch: SMMU id0=%08x id1=%08x id2=%08x\n",id0,id1,id2);
    unsigned count=id0&255,banks=id1&255,pages=1u<<(((id1>>28)&7)+1);
    if(!(id0&(1u<<27)) || (id1>>31) || !count || count>128 || !banks ||
        banks>128 || (pages+banks)*4096u>0xd0000u) {
        snapshotLog("AmethystTouch: SMMU geometry unsupported; survey stopped\n");
        map->release();return;
    }
    const unsigned streams[]={0x563,0x576,0x60};
    for(unsigned stream:streams) {
        unsigned matches=0;
        for(unsigned slot=0;slot<count;++slot) {
            uint32_t smr=load32(base+0x800+4*slot);
            if(!(smr&0x80000000u))continue;
            unsigned mask=(smr>>16)&0x7fff;
            if(((smr^stream)&0xffffu&~mask)!=0)continue;
            ++matches;
            uint32_t route=load32(base+0xc00+4*slot);
            snapshotLog("AmethystTouch: sid=%03x slot=%u smr=%08x s2cr=%08x\n",stream,slot,smr,route);
            unsigned bank=route&255,type=(route>>16)&3;
            if(type==0 && bank<banks) {
                snapshotLog("AmethystTouch: sid=%03x bank=%u sctlr=%08x cbar=%08x cba2r=%08x\n",
                    stream,bank,load32(base+(pages+bank)*4096u),
                    load32(base+0x1000+4*bank),load32(base+0x1800+4*bank));
            }
        }
        snapshotLog("AmethystTouch: sid=%03x matches=%u\n",stream,matches);
    }
    unsigned preflight=0;
    if(PE_parse_boot_argn("amethyst-touch-dma-preflight",&preflight,sizeof preflight) && preflight==1) {
        uint8_t *tx=nullptr,*rx=nullptr;uint64_t dma=0;
        int status=amethyst_touch_dma_prepare(&tx,&rx,&dma);
        snapshotLog("AmethystTouch: DMA allocation rc=%d pa=%llx tx=%p rx=%p bytes=%u\n",
            status,(unsigned long long)dma,tx,rx,AmethystTouchDmaBytes);
        if(!status) {
            AmethystTouchRoute plan=amethyst_touch_route(smmuRead,reinterpret_cast<void *>(base),dma,AmethystTouchDmaBytes);
            snapshotLog("AmethystTouch: DMA plan valid=%d slot=%u bank=%u reuse=%d; no commit\n",
                plan.valid,plan.slot,plan.bank,plan.reuse);
        }
    }
    map->release();
    amethyst_touch_loopback_stage();
}
}

extern "C" void amethyst_touch_snapshot(void);
extern "C" void amethyst_buttons_hid_start(void) __attribute__((weak_import));
extern "C" void sheng_gpu_power_probe(void) __attribute__((weak_import));   // GpuPowerProbe.cpp (asteroids), V42
extern "C" void sheng_cpufreq_probe(void) __attribute__((weak_import));
extern "C" void sheng_cpufreq_poll(void) __attribute__((weak_import));
extern "C" uint32_t amethyst_sigp_disabled_mask(void) __attribute__((weak_import));
extern "C" volatile uint32_t armcpu_core_sgi[8] __attribute__((weak_import));
extern "C" volatile uint64_t armcpu_ipi_sent __attribute__((weak_import));
extern "C" volatile uint32_t amethyst_hid_events[8] __attribute__((weak_import));
extern "C" volatile int amethyst_hid_results[8] __attribute__((weak_import));
extern "C" volatile uint32_t amethyst_hid_published_flags __attribute__((weak_import));
extern "C" volatile uint32_t amethyst_touch_hid_published __attribute__((weak_import));
extern "C" volatile uint32_t amethyst_touch_hid_downs __attribute__((weak_import));
extern "C" volatile uint32_t amethyst_touch_hid_shorts __attribute__((weak_import));
extern "C" volatile int amethyst_touch_hid_last[2] __attribute__((weak_import));
extern "C" volatile uint32_t native_touch_edge_stats[4] __attribute__((weak_import));   // NativeHidInput: installed, begun, at edge, skipped
extern "C" void amethyst_touch_snapshot(void) {
    // HID delivery (V30): NativeHidInput events (2 started, 3 driver attached, 6 touch reports), the
    // Built-In/PrimaryUsage publication, and what the touch thread handed over.
    if(&amethyst_hid_events && &amethyst_touch_hid_published) {
        amethyst_touch_row_set(49,"AmethystHid: drv=%u/%d rep=%u/%d btn=%u/%u pub=%u sent=%u dn=%u sh=%u edge=%u/%u/%u/%u last=%d,%d\n",
            amethyst_hid_events[3],amethyst_hid_results[3],
            amethyst_hid_events[6],amethyst_hid_results[6],amethyst_hid_events[4],amethyst_hid_events[5],amethyst_hid_published_flags,
            amethyst_touch_hid_published,amethyst_touch_hid_downs,&amethyst_touch_hid_shorts?amethyst_touch_hid_shorts:0,
            &native_touch_edge_stats?native_touch_edge_stats[0]:9,&native_touch_edge_stats?native_touch_edge_stats[1]:0,
            &native_touch_edge_stats?native_touch_edge_stats[2]:0,&native_touch_edge_stats?native_touch_edge_stats[3]:0,
            amethyst_touch_hid_last[0],amethyst_touch_hid_last[1]);
    }
    // SMP signalling: cores XNU cannot signal (SIGPdisabled; a stackshot then runs with them unstopped)
    // and IPIs each core has taken. Expect sigpoff=00 with armcpu-self-ipi=1.
    if(amethyst_sigp_disabled_mask && &armcpu_core_sgi) {
        unsigned self=0;PE_parse_boot_argn("armcpu-self-ipi",&self,sizeof self);
        amethyst_touch_row_set(48,"AmethystSmp: self-ipi=%u sigpoff=%02x sent=%llu ipi %u %u %u %u %u %u %u %u\n",
            self,amethyst_sigp_disabled_mask(),&armcpu_ipi_sent?(unsigned long long)armcpu_ipi_sent:0ULL,
            armcpu_core_sgi[0],armcpu_core_sgi[1],armcpu_core_sgi[2],armcpu_core_sgi[3],
            armcpu_core_sgi[4],armcpu_core_sgi[5],armcpu_core_sgi[6],armcpu_core_sgi[7]);
    }
    // CPU governor (sheng-cpufreq): mapped once, polled every log round so each domain is voted once it
    // reads enabled and the [clocks] row stays current.
    if(sheng_cpufreq_probe)sheng_cpufreq_probe();
    if(sheng_cpufreq_poll)sheng_cpufreq_poll();
    if(amethyst_buttons_hid_start)amethyst_buttons_hid_start();
    // GPU (V42): asteroids' staged probe, only when sheng-gpu-power is given (the file defaults to mode 19).
    {unsigned gpu=0;if(sheng_gpu_power_probe && PE_parse_boot_argn("sheng-gpu-power",&gpu,sizeof gpu) && gpu)sheng_gpu_power_probe();}
    // Independent read-only button transport discovery; no observer commands.
    unsigned buttons=0;
    static bool buttonsStarted=false;
    if(PE_parse_boot_argn("amethyst-buttons-survey",&buttons,sizeof buttons) && buttons==1 &&
        !__atomic_exchange_n(&buttonsStarted,true,__ATOMIC_ACQ_REL)) {
        // ADT reg0xb400000 is relative to arm-io base0x1000000.
        // Volcano DTS physical core range: 0xc400000/0x3000.
        IOMemoryMap *core=mapReadOnly(0x0c400000,0x3000);
        if(!core || !core->getVirtualAddress()) {
            snapshotLog("AmethystButtons: SPMI core mapping failed\n");
            if(core)core->release();
        } else {
            uintptr_t base=core->getVirtualAddress();
            uint32_t version=load32(base),features=load32(base+4);
            unsigned count=features&0x7ff;
            snapshotLog("AmethystButtons: core=0c400000 version=%08x features=%08x apids=%u\n",
                version,features,count);
            if(version>=0x70000000u && version<0x80000000u && count && count<=1024) {
                unsigned matches=0;
                for(unsigned i=0;i<count;++i) {
                    uint32_t entry=load32(base+0x2000+4*i);
                    unsigned ppid=(entry>>8)&0xfff;
                    // Survey all SIDs for PON13, GPIO6/8d and PMIC revision01.
                    if(entry && ((ppid&255)==0x13 || (ppid&255)==0x8d || (ppid&255)==1)) {
                        ++matches;
                        snapshotLog("AmethystButtons: apid=%u ppid=%03x map=%08x\n",i,ppid,entry);
                    }
                }
                snapshotLog("AmethystButtons: matches=%u; no bus commands\n",matches);
                unsigned identity=0;
                if(PE_parse_boot_argn("amethyst-buttons-identity",&identity,sizeof identity) && identity==1) {
                    IODeviceMemory *memory=IODeviceMemory::withRange(0x0c440000,0x8000);
                    IOMemoryMap *observer=memory?memory->map(kIOMapInhibitCache):nullptr;
                    if(memory)memory->release();
                    if(observer && observer->getVirtualAddress()) {
                        bool failed=false;
                        for(unsigned sid=0;sid<16 && !failed;++sid) {
                            unsigned apid=0,found=0;
                            for(unsigned i=0;i<count;++i) {
                                uint32_t entry=load32(base+0x2000+4*i);
                                if(entry && ((entry>>8)&0xfff)==((sid<<8)|1)) {apid=i;++found;}
                            }
                            if(!found)continue;
                            if(found!=1) {snapshotLog("AmethystButtons: sid=%u ambiguous identity map\n",sid);continue;}
                            uint8_t bytes[6]={};uint32_t sts=0;int rc=0;
                            for(unsigned reg=0;reg<6 && !rc;++reg)
                                rc=amethyst_spmi_read8(reinterpret_cast<void *>(observer->getVirtualAddress()),
                                    observerRead,observerWrite,observerDelay,apid,reg,&bytes[reg],&sts);
                            snapshotLog("AmethystButtons: sid=%u apid=%u id rc=%d sts=%08x rev=%02x%02x%02x%02x type=%02x subtype=%02x\n",
                                sid,apid,rc,sts,bytes[0],bytes[1],bytes[2],bytes[3],bytes[4],bytes[5]);
                            failed=rc!=0;
                        }
                    } else snapshotLog("AmethystButtons: observer mapping failed\n");
                    if(observer)observer->release();
                }
            } else snapshotLog("AmethystButtons: unsupported arbiter geometry; no commands\n");
            core->release();
        }
    }
    unsigned enabled=0;
    if(!PE_parse_boot_argn("amethyst-touch-snapshot",&enabled,sizeof enabled) ||
        (enabled!=1 && enabled!=2))return;
    static bool started=false;
    if(__atomic_exchange_n(&started,true,__ATOMIC_ACQ_REL))return;
    // gcc-volcano.c: wrap0 core2x/core/m-ahb/s-ahb/s0 and application vote.
    const uint32_t offsets[]={0x23018,0x23008,0x23000,0x23004,0x18004,0x52008};
    uint32_t clocks[6]={};
    for(unsigned i=0;i<6;++i) {
        if(!readRegister(0x100000+(offsets[i]&~4095u),offsets[i]&4095,clocks[i])) {
            snapshotLog("AmethystTouch: clock map failed offset=%x\n",offsets[i]);return;
        }
        snapshotLog("AmethystTouch: GCC+%x=%08x\n",offsets[i],clocks[i]);
    }
    // Read GPIO state without changing mux, interrupt or reset levels.
    const unsigned pins[]={0,1,2,3,70,87};
    for(unsigned pin:pins) {
        uint32_t control=0,io=0;
        uint64_t page=0xf100000ull+pin*4096ull;
        if(!readRegister(page,0,control) || !readRegister(page,4,io)) {
            snapshotLog("AmethystTouch: pin map failed gpio=%u\n",pin);return;
        }
        snapshotLog("AmethystTouch: gpio%u control=%08x io=%08x\n",pin,control,io);
    }
    if(enabled==2) {
        uint32_t before=0,after=0;
        bool voted=voteClocks(before,after);
        snapshotLog("AmethystTouch: clock-vote ok=%u before=%08x after=%08x mask=%08x\n",
            voted,before,after,AmethystQup0VoteMask);
        if(!voted)return;
        IOMemoryMap *statusMaps[2]={mapReadOnly(0x123000,4096),mapReadOnly(0x118000,4096)};
        if(!statusMaps[0] || !statusMaps[1] || !statusMaps[0]->getVirtualAddress() ||
            !statusMaps[1]->getVirtualAddress()) {
            if(statusMaps[0])statusMaps[0]->release();
            if(statusMaps[1])statusMaps[1]->release();
            snapshotLog("AmethystTouch: status mapping failed\n");return;
        }
        // Leave additive votes in place until reboot. Never clear a shared
        // vote on failure: another consumer may now require the same clock.
        unsigned attempt=0;
        for(;attempt<2000;++attempt) {
            for(unsigned i=0;i<5;++i) {
                uintptr_t base=statusMaps[i==4?1:0]->getVirtualAddress();
                clocks[i]=load32(base+(offsets[i]&4095));
            }
            if(amethyst_qup0_running(clocks))break;
            IODelay(10);
        }
        statusMaps[0]->release();statusMaps[1]->release();
        snapshotLog("AmethystTouch: clock-wait attempts=%u max=2000\n",attempt);
        for(unsigned i=0;i<5;++i)
            snapshotLog("AmethystTouch: post GCC+%x=%08x\n",offsets[i],clocks[i]);
    }
    bool running=amethyst_qup0_running(clocks);
    if(!running){snapshotLog("AmethystTouch: SE0 skipped: clock-off observed\n");return;}
    IOMemoryMap *map=mapReadOnly(0xa80000,0x4000);
    if(!map){snapshotLog("AmethystTouch: SE0 map failed\n");return;}
    uintptr_t base=map->getVirtualAddress();
    if(base && map->getLength()>=0x4000) {
        snapshotLog("AmethystTouch: SE0 status=%08x if-disable=%08x fw=%08x hw=%08x\n",
            load32(base+0x40),load32(base+0x64),load32(base+0x68),load32(base+0xe24));
    }
    map->release();
    surveySmmu();
}
