// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <IOKit/IOLib.h>
#include <IOKit/IODeviceMemory.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include "AmethystButtons.h"
#include "AmethystSpmiRead.h"
extern "C" void snapshotLog(const char *,...);
namespace {
uintptr_t observer;
void (*sink)(void *,uint32_t);
void *sinkContext;
IOLock *sinkLock;
uint8_t current;
uint32_t rd(void *p,unsigned o) {
    uint32_t v;uintptr_t a=reinterpret_cast<uintptr_t>(p)+o;
    __asm__ volatile("ldr %w0, [%1]":"=r"(v):"r"(a):"memory");return v;
}
void wr(void *p,unsigned o,uint32_t v) {
    uintptr_t a=reinterpret_cast<uintptr_t>(p)+o;
    __asm__ volatile("str %w0, [%1]\n\tdsb sy"::"r"(v),"r"(a):"memory");
}
void delay(void *){IODelay(1);}
IOMemoryMap *map(uint64_t pa,unsigned size,IOOptionBits flags) {
    auto *m=IODeviceMemory::withRange(pa,size);if(!m)return nullptr;
    auto *v=m->map(flags);m->release();return v;
}
int find(uintptr_t base,unsigned count,unsigned ppid) {
    int result=-1;
    for(unsigned i=0;i<count;++i) {
        uint32_t m=rd(reinterpret_cast<void *>(base),0x2000+4*i);
        if(m && ((m>>8)&4095)==ppid) {if(result>=0)return -1;result=i;}
    }
    return result;
}
void worker(void *,wait_result_t) {
    auto *core=map(0xc400000,0x3000,kIOMapReadOnly|kIOMapInhibitCache);
    auto *obs=map(0xc440000,0x8000,kIOMapInhibitCache);
    if(!core || !obs || !core->getVirtualAddress() || !obs->getVirtualAddress()) {
        snapshotLog("AmethystButtons: polling map failed\n");
        if(core)core->release();if(obs)obs->release();thread_terminate(current_thread());return;
    }
    uintptr_t base=core->getVirtualAddress();observer=obs->getVirtualAddress();
    unsigned version=rd(reinterpret_cast<void *>(base),0),count=rd(reinterpret_cast<void *>(base),4)&2047;
    int pon=-1,gpio=-1;
    if(version>=0x70000000 && version<0x80000000 && count && count<=1024) {
        pon=find(base,count,0x013);
        // Amethyst DTS c7bccfee: pmxr2230.dtsi reg=<1 SPMI_USID>,
        // volcano-pmic-overlay.dtsi GPIO6 active-low: PID0x88+5.
        gpio=find(base,count,0x18d);
    }
    core->release();
    snapshotLog("AmethystButtons: polling pon=%d gpio=%d version=%08x\n",pon,gpio,version);
    if(pon>=0) {
        AmethystButtonsFilter filter={};unsigned errors=0;
        for(;;) {
            uint8_t p=0,g=1,report=0;uint32_t status=0;
            int rc=amethyst_spmi_read8(reinterpret_cast<void *>(observer),rd,wr,delay,pon,16,&p,&status);
            if(!rc && gpio>=0)rc=amethyst_spmi_read8(reinterpret_cast<void *>(observer),rd,wr,delay,gpio,16,&g,&status);
            if(amethyst_buttons_step(&filter,!rc,amethyst_buttons_mask(p,g),&report)) {
                IOLockLock(sinkLock);current=report;if(sink)sink(sinkContext,current);IOLockUnlock(sinkLock);
                snapshotLog("AmethystButtons: report=%02x pon=%02x gpio=%02x rc=%d\n",report,p,g,rc);
            }
            if(rc && ++errors==1)snapshotLog("AmethystButtons: polling read rc=%d status=%08x; released keys\n",rc,status);
            if(rc)break; // No repeated commands after a timeout or denied access.
            IOSleep(10);
        }
    }
    obs->release();thread_terminate(current_thread());
}
}
extern "C" void native_buttons_set_sink(void (*callback)(void *,uint32_t),void *context);
extern "C" void native_buttons_set_sink(void (*callback)(void *,uint32_t),void *context) {
    // Called serially by the HID service lifecycle; worker starts only here.
    if(!sinkLock)sinkLock=IOLockAlloc();
    if(!sinkLock)return;
    IOLockLock(sinkLock);sink=callback;sinkContext=context;
    if(sink)sink(sinkContext,current);IOLockUnlock(sinkLock);
    static bool started=false;unsigned enabled=0;
    if(callback && PE_parse_boot_argn("amethyst-buttons",&enabled,sizeof enabled) && enabled==1 &&
        !__atomic_exchange_n(&started,true,__ATOMIC_ACQ_REL)) {
        thread_t thread=THREAD_NULL;
        kern_return_t rc=kernel_thread_start(worker,nullptr,&thread);
        if(rc==KERN_SUCCESS)thread_deallocate(thread);
        snapshotLog("AmethystButtons: polling thread rc=%d\n",rc);
    }
}
