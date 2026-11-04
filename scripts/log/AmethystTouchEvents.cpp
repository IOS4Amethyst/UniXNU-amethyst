// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opt-in (amethyst-touch-events=1 coordinates, =2 host-computing frames) Goodix poller, started by the Goodix
// stage after a valid identity read. It owns the SE0 engine and the touch DMA
// buffers for the rest of the boot (the caller retains them). Every 4 ms it
// samples the interrupt line (GPIO70, active low); when low it reads the event
// block (GoodixBerlinEvent.h) and, only when the head checksum is valid and the
// status is nonzero, acknowledges it with one zero byte, as the vendor driver does.
// Results go to two live rows (46 and 47) that BootLog copies every round.
// With sheng-touch-hid=1 (V30) the frame detector's press is published to TouchState
// (native_touch_*, seqlock) every frame while down and once on release; UniXNU's
// NativeHidInput turns it into Touch Screen reports for backboardd.
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <string.h>
#include "AmethystTouchPower.h"

extern "C" {
#include "GeniSpi.h"
#include "GoodixBerlinSpi.h"
#include "GoodixBerlinEvent.h"
#include "GoodixBerlinIcInfo.h"
#include "GoodixBerlinFrame.h"
#include "GoodixBerlinDiff.h"
int amethyst_goodix_read(struct geni_spi *spi,uint32_t address,uint8_t *out,
    size_t bytes,uint8_t *tx,uint8_t *rx,size_t capacity);
void amethyst_touch_row_set(unsigned index,const char *format,...);
int amethyst_touch_events_start(struct geni_spi *spi,unsigned mode,const struct GoodixBerlinIcInfo *info);
int boot_log_export_put(uint32_t generation,uint32_t total,uint32_t offset,const void *data,uint32_t size)
    __attribute__((weak_import));
extern volatile uint32_t native_touch_seq,native_touch_count;
extern volatile int native_touch_xs[12],native_touch_ys[12],native_touch_ids[12];
void native_touch_published(void);
volatile uint32_t amethyst_touch_hid_published,amethyst_touch_hid_downs,amethyst_touch_hid_shorts;
volatile int amethyst_touch_hid_last[2];
}

namespace {
struct geni_spi *engine;
uint32_t eventBlock=GoodixBerlinEventAddress;   // from IC_INFO touch_data_addr when it parsed
uintptr_t irqBase;
uint8_t tx[128],rx[128],block[GoodixBerlinEventBytes];
// Frame mode (V17): the firmware boots in host touch computing mode and holds
// GPIO70 low while a frame waits (V13/V14: low 97% because nothing cleared it).
uint32_t frameBlock;
struct GoodixBerlinFrameLayout layout;
size_t frameBytes;
uint8_t ftx[GoodixBerlinReadPrefix+GoodixBerlinFrameMax],frx[sizeof ftx],frame[GoodixBerlinFrameMax];
struct FrameStats {
    unsigned polls,lows,reads,readErrors,notReady,headBad,clears,clearErrors;
    struct GoodixBerlinFrameStats last;
    int16_t peak;unsigned peakIndex,peakFrame;   // largest grid value seen, where and when
    int16_t floor;unsigned floorIndex;            // smallest
} fstats;
GoodixDiffState diff;
// Frame recorder (V20): the last kRingFrames grids, exported through BootLog's export
// region every few seconds so the raw data can be studied off the phone. Blob:
// "AMTFRM01", u32 drv, sen, entry bytes, count, newest sequence, frames/s*100, 0;
// then count entries oldest first: u32 ms since start, u32 sequence, drv*sen LE16.
// V28: amethyst-touch-record-until=<s> stops recording that many seconds after the
// touch start, so the export holds a chosen window (V26/V27 false presses at ~58-99 s).
enum { kRingFrames=4096, kExportEvery=8000 };
uint64_t recordUntilMs;
// V35: amethyst-touch-record-press=<s> stops recording that many seconds after the first press, so the
// ring (4096 frames, ~48 s at 85 fps) holds the frames around the user's touches.
unsigned recordPressS;
uint8_t *ring;size_t entryBytes;unsigned ringCount,ringNext,sequence,exported,exportGeneration;
uint64_t startTicks,lastExportMs;int exportStatus;
uint64_t nowMs() {
    uint64_t t,f;
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(t));
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(f));
    return f?(t-startTicks)*1000/f:0;
}
void ringPut(const uint8_t *grid,uint32_t ms) {
    if(!ring)return;
    uint8_t *e=ring+(size_t)ringNext*entryBytes;
    memcpy(e,&ms,4);memcpy(e+4,&sequence,4);
    memcpy(e+8,grid,entryBytes-8);
    ringNext=(ringNext+1)%kRingFrames;
    if(ringCount<kRingFrames)++ringCount;
    ++sequence;
}
void ringExport(uint64_t ms) {
    if(!ring || !boot_log_export_put || !ringCount)return;
    struct { char magic[8]; uint32_t drv,sen,entry,count,newest,rate,zero; } head;
    memcpy(head.magic,"AMTFRM01",8);
    head.drv=layout.drvNum;head.sen=layout.senNum;head.entry=(uint32_t)entryBytes;head.count=ringCount;
    head.newest=sequence-1;head.rate=ms?(uint32_t)((uint64_t)sequence*100000/ms):0;head.zero=0;
    const uint32_t total=(uint32_t)(sizeof head+(size_t)ringCount*entryBytes);
    const uint32_t generation=++exportGeneration;
    int rc=boot_log_export_put(generation,total,0,&head,sizeof head);
    uint32_t offset=sizeof head;
    const unsigned first=ringCount<kRingFrames?0:ringNext;
    // Oldest first: [first, end) then [0, first).
    const unsigned spans[2][2]={{first,ringCount<kRingFrames?ringCount:kRingFrames},{0,ringCount<kRingFrames?0:first}};
    for(unsigned s=0;s<2 && !rc;++s) {
        const uint32_t bytes=(uint32_t)((spans[s][1]-spans[s][0])*entryBytes);
        if(!bytes)continue;
        rc=boot_log_export_put(generation,total,offset,ring+(size_t)spans[s][0]*entryBytes,bytes);
        offset+=bytes;
    }
    exportStatus=rc;
    if(!rc)exported=sequence;
}

struct Stats {
    unsigned polls,irqs,reads,readErrors,badHeads,acks,ackErrors,touches,lifts,badPoints;
    unsigned minX,minY,maxX,maxY,maxCount;
    struct GoodixBerlinEvent last;
} stats;

uint32_t irqLevel() {
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(irqBase+AmethystTlmmIo) : "memory");
    return value&1;
}
int acknowledge() {
    const uint8_t zero=0;
    size_t total=goodix_berlin_write_frame(eventBlock,&zero,1,tx,sizeof tx);
    if(!total)return GENI_ERR_PARAM;
    if(engine->se.rd(engine->se.ctx,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE)return GENI_ERR_IO;
    return geni_spi_transfer(engine,tx,rx,total,0);
}
void publish() {
    const struct GoodixBerlinEvent &e=stats.last;
    amethyst_touch_row_set(46,
        "AmethystTouch: ev @%x low=%u/%u rd=%u/%u bad=%u/%u ack=%u/%u down=%u up=%u max=%u x=%u-%u y=%u-%u\n",
        eventBlock,stats.irqs,stats.polls,stats.reads,stats.readErrors,stats.badHeads,stats.badPoints,stats.acks,stats.ackErrors,
        stats.touches,stats.lifts,stats.maxCount,stats.minX,stats.maxX,stats.minY,stats.maxY);
    amethyst_touch_row_set(47,
        "AmethystTouch: last st=%02x rq=%02x n=%u p0 id=%u t=%u x=%u y=%u w=%u p1 id=%u x=%u y=%u raw=%02x%02x%02x%02x%02x%02x%02x%02x\n",
        e.status,e.request,e.count,e.points[0].id,e.points[0].type,e.points[0].x,e.points[0].y,e.points[0].w,
        e.points[1].id,e.points[1].x,e.points[1].y,block[0],block[1],block[2],block[3],block[4],block[5],
        block[6],block[7]);
}
void handle() {
    int rc=amethyst_goodix_read(engine,eventBlock,block,sizeof block,tx,rx,sizeof tx);
    if(rc){++stats.readErrors;return;}
    ++stats.reads;
    struct GoodixBerlinEvent event;
    if(!goodix_berlin_event_parse(block,sizeof block,&event)){++stats.badHeads;stats.last=event;return;}
    if(event.status&0x80) {
        if(!event.pointsOk && event.count)++stats.badPoints;
        else if(!event.count)++stats.lifts;
        else {
            ++stats.touches;
            if(event.count>stats.maxCount)stats.maxCount=event.count;
            for(unsigned i=0;i<event.count;++i) {
                const GoodixBerlinContact &p=event.points[i];
                if(p.x<stats.minX)stats.minX=p.x;
                if(p.y<stats.minY)stats.minY=p.y;
                if(p.x>stats.maxX)stats.maxX=p.x;
                if(p.y>stats.maxY)stats.maxY=p.y;
            }
        }
    }
    stats.last=event;
    if(event.status) {
        if(acknowledge())++stats.ackErrors;
        else ++stats.acks;
    }
}
// V15: in coordinate mode GPIO70 idles high and its pulses are shorter than a
// 4 ms poll (0 of ~40000 samples low while the user tapped). Poll the 8-byte
// event head instead and read the whole block only when its status is set.
void worker(void *,wait_result_t) {
    publish();
    uint8_t head[GoodixBerlinEventHead];
    for(unsigned idle=0;;) {
        ++stats.polls;
        if(!irqLevel())++stats.irqs;
        int rc=amethyst_goodix_read(engine,eventBlock,head,sizeof head,tx,rx,sizeof tx);
        if(rc) {
            ++stats.readErrors;
            if(!(stats.readErrors&63))publish();
            IOSleep(50);
            continue;
        }
        if(head[0]) {
            handle();
            publish();
            IOSleep(stats.badHeads>stats.reads/2 && stats.badHeads>8?50:4);
            idle=0;
        } else {
            if(!(stats.polls&255))publish();
            IOSleep(++idle>2500?8:4);
        }
    }
}
}

void framePublish() {
    const GoodixDiffState &d=diff;
    amethyst_touch_row_set(46,
        "AmethystTouch: fr %ux%u rd=%u/%u nr=%u hb=%u clr=%u/%u act=%u pr=%u st=%u iso=%u ext=%d@%u now=%d@%u\n",
        layout.drvNum,layout.senNum,fstats.reads,fstats.readErrors,fstats.notReady,fstats.headBad,
        fstats.clears,fstats.clearErrors,d.active,d.presses,d.stuck,d.isolated,d.extreme,d.extremeIndex,d.peak,d.peakIndex);
    // Presses in order: newest is (pr-1)%6. index = node of the strongest diff, value signed.
    amethyst_touch_row_set(47,
        "AmethystTouch: press %d %u,%u:%d %u,%u:%d %u,%u:%d %u,%u:%d %u,%u:%d %u,%u:%d\n",
        d.pressed,d.ring[0].row16,d.ring[0].col16,d.ring[0].value,d.ring[1].row16,d.ring[1].col16,d.ring[1].value,
        d.ring[2].row16,d.ring[2].col16,d.ring[2].value,d.ring[3].row16,d.ring[3].col16,d.ring[3].value,
        d.ring[4].row16,d.ring[4].col16,d.ring[4].value,d.ring[5].row16,d.ring[5].col16,d.ring[5].value);
}
int frameClear() {
    const uint8_t zero=0;
    size_t total=goodix_berlin_write_frame(frameBlock,&zero,1,ftx,sizeof ftx);
    if(!total)return GENI_ERR_PARAM;
    if(engine->se.rd(engine->se.ctx,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE)return GENI_ERR_IO;
    return geni_spi_transfer(engine,ftx,frx,total,0);
}
bool hidEnabled,hidDown;
// One finger (or none) to TouchState. Each press gets a new identifier so iOS sees a new touch.
void hidPublish() {
    const bool down=diff.pressed!=0;
    if(!down && !hidDown)return;
    uint32_t seq=__atomic_load_n(&native_touch_seq,__ATOMIC_RELAXED);
    __atomic_store_n(&native_touch_seq,seq+1,__ATOMIC_RELEASE);
    __asm__ volatile("dmb ish" ::: "memory");
    if(down) {
        int x,y;goodix_diff_screen(&diff,&diff.live,&x,&y);
        native_touch_xs[0]=x;native_touch_ys[0]=y;native_touch_ids[0]=(int)(diff.presses%200)+1;
        amethyst_touch_hid_last[0]=x;amethyst_touch_hid_last[1]=y;
        if(!hidDown)amethyst_touch_hid_downs=amethyst_touch_hid_downs+1;
    }
    native_touch_count=down?1:0;
    __asm__ volatile("dmb ish" ::: "memory");
    __atomic_store_n(&native_touch_seq,seq+2,__ATOMIC_RELEASE);
    native_touch_published();
    hidDown=down;
    amethyst_touch_hid_shorts=diff.shorts;
    amethyst_touch_hid_published=amethyst_touch_hid_published+1;
}
void frameWorker(void *,wait_result_t) {
    framePublish();
    for(;;) {
        ++fstats.polls;
        if(irqLevel()) {
            if(!(fstats.polls&255))framePublish();
            IOSleep(4);
            continue;
        }
        ++fstats.lows;
        int rc=amethyst_goodix_read(engine,frameBlock,frame,frameBytes,ftx,frx,sizeof ftx);
        if(rc) {
            ++fstats.readErrors;
            if(!(fstats.readErrors&63))framePublish();
            IOSleep(50);
            continue;
        }
        ++fstats.reads;
        if(!(frame[0]&0x80)) {            // line low but no frame: do not write
            ++fstats.notReady;
            if(!(fstats.notReady&63))framePublish();
            IOSleep(4);
            continue;
        }
        goodix_berlin_frame_analyze(frame,frameBytes,&layout,&fstats.last);
        if(!fstats.last.headOk)++fstats.headBad;
        bool press=false;
        if(fstats.last.gridOk) {
            press=goodix_diff_frame(&diff,frame+goodix_berlin_frame_grid_offset(&layout));
            if(hidEnabled)hidPublish();
            if(recordPressS && !recordUntilMs && diff.presses)recordUntilMs=nowMs()+(uint64_t)recordPressS*1000;
            if(!recordUntilMs || nowMs()<recordUntilMs)
                ringPut(frame+goodix_berlin_frame_grid_offset(&layout),(uint32_t)nowMs());
            if(fstats.last.max>fstats.peak){fstats.peak=fstats.last.max;fstats.peakIndex=fstats.last.maxIndex;fstats.peakFrame=fstats.reads;}
            if(fstats.last.min<fstats.floor){fstats.floor=fstats.last.min;fstats.floorIndex=fstats.last.minIndex;}
        }
        if(frameClear())++fstats.clearErrors;
        else ++fstats.clears;
        if(press || !(fstats.reads&15))framePublish();
        const uint64_t ms=nowMs();
        if(ms-lastExportMs>=kExportEvery && sequence!=exported){lastExportMs=ms;ringExport(ms);framePublish();}
        IOSleep(2);
    }
}

// Returns 0 when the poller owns the engine (caller must retain mappings and buffers).
extern "C" int amethyst_touch_events_start(struct geni_spi *spi,unsigned mode,const struct GoodixBerlinIcInfo *info) {
    uint32_t eventAddress=info?info->touchDataAddress:GoodixBerlinEventAddress;
    if(mode==2) {
        if(!info || !info->frameDataAddress) {
            amethyst_touch_row_set(46,"AmethystTouch: fr stopped: no IC_INFO frame address\n");
            return -22;
        }
        layout.headLen=info->frameHeadLen;layout.attrLen=info->fwAttrLen;layout.logLen=info->fwLogLen;
        layout.drvNum=info->drvNum;layout.senNum=info->senNum;
        frameBytes=goodix_berlin_frame_bytes(&layout);
        frameBlock=info->frameDataAddress;
        if(!frameBytes) {
            amethyst_touch_row_set(46,"AmethystTouch: fr stopped: layout h%u a%u l%u %ux%u\n",
                layout.headLen,layout.attrLen,layout.logLen,layout.drvNum,layout.senNum);
            return -22;
        }
    }
    IODeviceMemory *memory=IODeviceMemory::withRange(
        AmethystTlmmBase+AmethystPinIrq*AmethystTlmmStride,AmethystTlmmStride);
    IOMemoryMap *map=memory?memory->map(kIOMapInhibitCache):nullptr;
    if(memory)memory->release();
    if(!map || !map->getVirtualAddress()) {
        if(map)map->release();
        amethyst_touch_row_set(46,"AmethystTouch: ev stopped: irq map failed\n");
        return -12;
    }
    irqBase=map->getVirtualAddress();   // kept for the life of the boot
    engine=spi;
    eventBlock=eventAddress;
    memset(&stats,0,sizeof stats);
    stats.minX=stats.minY=0xffff;
    memset(&fstats,0,sizeof fstats);
    fstats.peak=INT16_MIN;fstats.floor=INT16_MAX;
    unsigned hid=0;
    hidEnabled=mode==2 && PE_parse_boot_argn("sheng-touch-hid",&hid,sizeof hid) && hid==1;
    goodix_diff_reset(&diff,mode==2?layout.drvNum:0,mode==2?layout.senNum:0);
    unsigned record=0;
    // V20: exporting from this thread while iOS read its root froze userspace at the
    // first UI frame; the recorder is now opt-in (amethyst-touch-record=1) for bench use.
    if(mode==2 && PE_parse_boot_argn("amethyst-touch-record",&record,sizeof record) && record==1) {
        entryBytes=8+2u*layout.drvNum*layout.senNum;
        ring=static_cast<uint8_t *>(IOMallocData(kRingFrames*entryBytes));   // kept for the boot
        ringCount=ringNext=sequence=exported=exportGeneration=0;
        uint64_t t;__asm__ volatile("mrs %0, cntvct_el0" : "=r"(t));startTicks=t;lastExportMs=0;exportStatus=ring?0:-12;
        unsigned until=0;
        if(PE_parse_boot_argn("amethyst-touch-record-until",&until,sizeof until))recordUntilMs=(uint64_t)until*1000;
        PE_parse_boot_argn("amethyst-touch-record-press",&recordPressS,sizeof recordPressS);
    }
    thread_t thread;
    kern_return_t rc=kernel_thread_start(mode==2?frameWorker:worker,nullptr,&thread);
    if(rc!=KERN_SUCCESS) {
        amethyst_touch_row_set(46,"AmethystTouch: ev stopped: thread rc=%d\n",rc);
        return -12;
    }
    thread_deallocate(thread);
    return 0;
}
