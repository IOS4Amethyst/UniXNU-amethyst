// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSKext.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>
#include <mach/kmod.h>
#include <pexpert/pexpert.h>
extern kmod_info_t g_kernel_kmod_info;
extern "C" {
void snapshotLog(const char *,...);
void native_hid_static_init(void) __asm__("__GLOBAL__sub_I_NativeHidInput.cpp");
IOService *native_hid_input_allocate(void);
OSReturn OSKextRegisterBoardClasses(const char *,void (*)(void)) __attribute__((weak_import));
kern_return_t OSKextLoadKextWithIdentifier(const char *);
// Called by the HID timer only after the event driver attaches. The existing
// driver emits a press and a release; this opt-in requests exactly one pair.
bool native_hid_input_requested(void) {
    static unsigned ticks=0;static bool sent=false;unsigned enabled=0;
    if(sent || !PE_parse_boot_argn("amethyst-home-once",&enabled,sizeof enabled) || enabled!=1)return false;
    // ponytail: allow 30 attached-driver timer ticks for userland to settle.
    // Replace with a UI readiness signal when one is available.
    if(++ticks<30)return false;
    sent=true;snapshotLog("AmethystButtons: one-shot Home requested\n");return true;
}
// TouchState sink (V30, from UniXNU-asteroids package/TouchState.cpp): NativeHidInput registers its
// touchNotify here when sheng-touch-hid=1; AmethystTouchEvents.cpp calls native_touch_published after
// each seqlocked finger set.
static void (*volatile touchSink)(void *);
static void *volatile touchSinkContext;
void native_touch_set_sink(void (*sink)(void *),void *context) {
    touchSink=nullptr;
    __asm__ volatile("dmb ish" ::: "memory");
    touchSinkContext=context;
    __asm__ volatile("dmb ish" ::: "memory");
    touchSink=sink;
}
void native_touch_published(void) {
    void (*sink)(void *)=touchSink;
    __asm__ volatile("dmb ish" ::: "memory");
    if(sink)sink(touchSinkContext);
}
// HID status for BootLog row 49: per event (0..7) count and last result; NativeHidInput events are
// 2 started, 3 event-driver poll (result 1 = attached), 4/5 button down/up, 6 touch report.
volatile uint32_t amethyst_hid_events[8];
volatile int amethyst_hid_results[8]={-1,-1,-1,-1,-1,-1,-1,-1};
volatile uint32_t amethyst_hid_published_flags;   // bit 0: Built-In/PrimaryUsage set on the event driver
// From UniXNU-asteroids package/HidInputProbe.cpp native_hid_live_checkpoint (property part only): backboardd
// drops digitizer events unless the sending event service has PrimaryUsagePage 0x0D / PrimaryUsage 4 and
// Built-In; the combined device lists its keyboard collection first, so the services would publish (1,6).
static IORegistryEntry *findEventDriver(IORegistryEntry *parent,unsigned depth) {
    if(!depth)return nullptr;
    OSIterator *it=parent->getChildIterator(gIOServicePlane);if(!it)return nullptr;
    IORegistryEntry *found=nullptr;
    while(OSObject *child=it->getNextObject()) {
        IORegistryEntry *entry=OSDynamicCast(IORegistryEntry,child);
        if(!entry)continue;
        if(entry->metaCast("IOHIDEventDriver")){found=entry;break;}
        if((found=findEventDriver(entry,depth-1)))break;
    }
    it->release();return found;
}
void native_hid_live_checkpoint(IOService *device) {
    static IORegistryEntry *publishedFor;
    if(!device || !device->getProperty("Built-In"))return;      // set by NativeHidInput only with touch on
    IORegistryEntry *driver=findEventDriver(device,4);
    if(!driver || publishedFor==driver)return;                    // once: each setProperty notifies clients
    publishedFor=driver;
    driver->setProperty("Built-In",kOSBooleanTrue);driver->setProperty("DisplayIntegrated",kOSBooleanTrue);
    driver->setProperty("PrimaryUsagePage",(unsigned long long)0x0d,32);driver->setProperty("PrimaryUsage",(unsigned long long)4,32);
    if(IORegistryEntry *interface=driver->getParentEntry(gIOServicePlane)) {
        interface->setProperty("Built-In",kOSBooleanTrue);interface->setProperty("DisplayIntegrated",kOSBooleanTrue);
        interface->setProperty("PrimaryUsagePage",(unsigned long long)0x0d,32);interface->setProperty("PrimaryUsage",(unsigned long long)4,32);
    }
    amethyst_hid_published_flags=amethyst_hid_published_flags|1;
}
void native_hid_input_observed(unsigned event,int result) {
    if(event<8){amethyst_hid_events[event]=amethyst_hid_events[event]+1;amethyst_hid_results[event]=result;}
    if(event==6)return;                                           // touch reports: counted only
    if(event==3) {
        static int previous=-1;
        if(previous!=result) {
            previous=result;snapshotLog("AmethystButtons: HID event-driver attached=%d\n",result);
        }
        return;
    }
    if(event==4 || event==5 || result)
        snapshotLog("AmethystButtons: HID event=%u rc=%d\n",event,result);
}
volatile uint32_t native_touch_seq,native_touch_count,native_touch_hid_reports;
volatile int native_touch_xs[12],native_touch_ys[12],native_touch_ids[12];
void amethyst_buttons_hid_start(void);
void amethyst_buttons_hid_start(void) {
    unsigned enabled=0;static bool started=false;
    if(!PE_parse_boot_argn("amethyst-buttons",&enabled,sizeof enabled) || enabled!=1 ||
        __atomic_exchange_n(&started,true,__ATOMIC_ACQ_REL))return;
    if(!OSKextRegisterBoardClasses) {
        snapshotLog("AmethystButtons: missing lock-safe class registration; HID not started\n");return;
    }
    int rc=OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDFamily");
    if(!rc)rc=OSKext::loadKextWithIdentifier("com.apple.iokit.IOHIDEventDriver",
        true,false,kOSKextExcludeNone,kOSKextExcludeNone,nullptr);
    snapshotLog("AmethystButtons: HID family load rc=%d\n",rc);
    if(rc)return;
    const OSSymbol *name=OSSymbol::withCString("IOHIDDevice");
    const OSMetaClass *meta=name?OSMetaClass::getMetaClassWithName(name):nullptr;
    if(name)name->release();
    if(!meta || meta->getClassSize()!=208) {
        snapshotLog("AmethystButtons: HID runtime ABI mismatch\n");return;
    }
    rc=OSKextRegisterBoardClasses(g_kernel_kmod_info.name,native_hid_static_init);
    snapshotLog("AmethystButtons: HID classes rc=%d\n",rc);if(rc)return;
    OSDictionary *match=IOService::serviceMatching("IOHIDResource");
    IOService *resource=match?IOService::waitForMatchingService(match,5000000000ULL):nullptr;
    if(match)match->release();
    if(!resource){snapshotLog("AmethystButtons: no HID resource\n");return;}
    IOService *device=native_hid_input_allocate();
    bool initialized=device && device->init();
    bool attached=initialized && device->attach(resource);
    bool running=attached && device->start(resource);
    snapshotLog("AmethystButtons: HID init=%u attached=%u running=%u\n",initialized,attached,running);
    if(!running && device) {if(attached)device->detach(resource);device->release();}
    resource->release();
    // ponytail: successful service lives until reboot; teardown awaits driver lifecycle integration.
}
}
