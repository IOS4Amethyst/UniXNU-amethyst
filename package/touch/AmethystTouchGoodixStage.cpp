// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opt-in (amethyst-touch-goodix=1) controller bring-up, run only after the
// internal loopback passed and returned its buffers. Powers the Goodix rails
// through RPMh, muxes the SPI pins, releases reset and follows Linux
// goodix_berlin_core.c: boot option handshake (8 x 0xAA at 0x10000, read back),
// 100 ms, version block 0x10014, IC_INFO at 0x10070 (event block address).
// The handshake is the only write; no firmware/config upload, no mode commands.
// Board facts: AmethystTouchPower.h.
// RPMh TCS handling follows rootstockos/UniXNU-asteroids AsteroidsTouchProbe.cpp
// (BSD-2-Clause-Patent): write only an idle, empty TCS; completion is the
// command's own status bit; a TCS still busy is left alone.
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include <string.h>
#include "AmethystTouchPower.h"

struct GoodixBerlinIcInfo;
extern "C" {
void snapshotLog(const char *format,...);
#include "GeniSpi.h"
#include "QcomRpmh.h"
#include "GoodixBerlinIdentity.h"
#include "GoodixBerlinIcInfo.h"
#include "GoodixBerlinEvent.h"
#include "GoodixBerlinCmd.h"
int amethyst_goodix_read(struct geni_spi *spi,uint32_t address,uint8_t *out,
    size_t bytes,uint8_t *tx,uint8_t *rx,size_t capacity);
int amethyst_goodix_write(struct geni_spi *spi,uint32_t address,const uint8_t *data,
    size_t bytes,uint8_t *tx,uint8_t *rx,size_t capacity);
int amethyst_touch_dma_claim(void);
int amethyst_touch_dma_idle(void);
int amethyst_touch_goodix_stage(struct geni_spi *spi);
extern uint32_t amethyst_touch_source_hz;
int amethyst_touch_events_start(struct geni_spi *spi,unsigned mode,const struct GoodixBerlinIcInfo *info);
}

namespace {
uint32_t rd(uintptr_t base,uint32_t offset) {
    uint32_t value;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(value) : "r"(base+offset) : "memory");
    return value;
}
void wr(uintptr_t base,uint32_t offset,uint32_t value) {
    __asm__ volatile("str %w0, [%1]\n\tdsb sy" :: "r"(value),"r"(base+offset) : "memory");
}
struct Map {
    IOMemoryMap *map=nullptr;
    uintptr_t va=0;
    bool open(uint64_t phys,uint64_t bytes,bool readOnly) {
        IODeviceMemory *memory=IODeviceMemory::withRange(phys,bytes);
        if(!memory)return false;
        map=memory->map(kIOMapInhibitCache|(readOnly?kIOMapReadOnly:0));
        memory->release();
        va=map?map->getVirtualAddress():0;
        return va!=0 && map->getLength()>=bytes;
    }
    ~Map(){if(map)map->release();}
};

int findRails(QcomRpmhResource &avdd,QcomRpmhResource &iovdd) {
    Map db;
    if(!db.open(AMETHYST_CMD_DB_BASE,AMETHYST_CMD_DB_BYTES,true))return -12;
    uint8_t *copy=static_cast<uint8_t *>(IOMallocData(AMETHYST_CMD_DB_BYTES));
    if(!copy)return -12;
    // Aligned 32-bit loads only: the region is uncached device memory.
    for(uint32_t i=0;i<AMETHYST_CMD_DB_BYTES/4;++i) {
        uint32_t word=rd(db.va,4*i);
        memcpy(copy+4*i,&word,4);
    }
    int status=qcom_cmd_db_find(copy,AMETHYST_CMD_DB_BYTES,AMETHYST_AVDD_RESOURCE,&avdd);
    if(!status)status=qcom_cmd_db_find(copy,AMETHYST_CMD_DB_BYTES,AMETHYST_IOVDD_RESOURCE,&iovdd);
    IOFreeData(copy,AMETHYST_CMD_DB_BYTES);
    return status;
}

int sendOne(uintptr_t rsc,const QcomRscLayout &l,uint32_t address,uint32_t value,uint32_t &cmdStatus) {
    const uint32_t base=AMETHYST_RSC_TCS_OFFSET+l.tcs_stride*AMETHYST_RSC_ACTIVE_TCS;
    if(!(rd(rsc,base+l.status)&1) || rd(rsc,base+l.control) || rd(rsc,base+l.cmd_enable))
        return -16;
    QcomRpmhWrite writes[16];
    unsigned n=qcom_rpmh_active_request(&l,AMETHYST_RSC_ACTIVE_TCS,&address,&value,1,writes,16);
    if(!n)return -22;
    for(unsigned i=0;i<n;++i)wr(rsc,AMETHYST_RSC_TCS_OFFSET+writes[i].offset,writes[i].value);
    bool done=false,idle=false;
    for(unsigned i=0;i<200 && !done;++i) {
        done=((cmdStatus=rd(rsc,base+l.cmd_data+4))&(1u<<16))!=0;
        if(!done)IODelay(100);
    }
    for(unsigned i=0;i<200 && !idle;++i) {
        idle=(rd(rsc,base+l.status)&1)!=0;
        if(!idle)IODelay(100);
    }
    if(!idle)return -11;
    unsigned m=qcom_rpmh_finish(&l,AMETHYST_RSC_ACTIVE_TCS,writes,16);
    for(unsigned i=0;i<m;++i)wr(rsc,AMETHYST_RSC_TCS_OFFSET+writes[i].offset,writes[i].value);
    return done?0:-110;
}
// brl_send_cmd: up to 6 sends, each polled up to 6 times 1 ms apart. Returns 0 on
// ack 0x80; ack holds the last ack byte read.
int sendCommand(struct geni_spi *spi,uint32_t cmdAddress,uint8_t cmd,uint8_t value,uint8_t &ack,
    uint8_t *tx,uint8_t *rx,size_t capacity) {
    uint8_t frame[GoodixBerlinCmdBytes],reply[GoodixBerlinCmdBytes];
    if(!goodix_berlin_cmd_frame(cmd,value,frame))return -22;
    ack=0xff;
    int rc=-110;
    for(unsigned send=0;send<6;++send) {
        rc=amethyst_goodix_write(spi,cmdAddress,frame,sizeof frame,tx,rx,capacity);
        if(rc)return rc;
        bool resend=false;
        for(unsigned poll=0;poll<6 && !resend;++poll) {
            rc=amethyst_goodix_read(spi,cmdAddress,reply,sizeof reply,tx,rx,capacity);
            if(rc)return rc;
            ack=reply[1];
            switch(goodix_berlin_cmd_ack(ack)) {
            case GoodixAckDone: IOSleep(4); return 0;
            case GoodixAckBusy: IOSleep(1); break;
            case GoodixAckOverflow: IOSleep(10); IOSleep(1); resend=true; break;
            case GoodixAckResend: IOSleep(1); resend=true; break;
            }
        }
        rc=-110;
    }
    return rc;
}
int voteRail(uintptr_t rsc,const QcomRscLayout &l,uint32_t address,uint32_t mv,uint32_t &cmdStatus) {
    uint32_t addresses[3],values[3];
    amethyst_rail_commands(address,mv,addresses,values);
    for(unsigned c=0;c<3;++c)
        if(int rc=sendOne(rsc,l,addresses[c],values[c],cmdStatus))return rc;
    return 0;
}
}

// Returns 0 when the engine is idle afterwards (mappings may be released),
// negative when it may still own the buffers (caller must retain everything).
extern "C" int amethyst_touch_goodix_stage(struct geni_spi *spi) {
    unsigned enabled=0;
    if(!PE_parse_boot_argn("amethyst-touch-goodix",&enabled,sizeof enabled) || enabled!=1)
        return 0;
    QcomRpmhResource avdd{},iovdd{};
    int status=findRails(avdd,iovdd);
    Map rsc,reset,irq,spiPins[4];
    bool mapped=rsc.open(AMETHYST_RSC_DRV2,0x10000,false) &&
        reset.open(AmethystTlmmBase+AmethystPinReset*AmethystTlmmStride,AmethystTlmmStride,false) &&
        irq.open(AmethystTlmmBase+AmethystPinIrq*AmethystTlmmStride,AmethystTlmmStride,false);
    for(unsigned i=0;i<4 && mapped;++i)
        mapped=spiPins[i].open(AmethystTlmmBase+amethyst_spi_pins[i].pin*AmethystTlmmStride,
            AmethystTlmmStride,false);
    uint32_t rscId=mapped?rd(rsc.va,0):0;
    if(status || !mapped || !avdd.address || !iovdd.address) {
        snapshotLog("AmethystTouch: goodix stopped before power: cmddb=%d avdd=%x iovdd=%x map=%d\n",
            status,avdd.address,iovdd.address,mapped);
        return 0;
    }
    // Controller held in reset while the rails come up (vendor order).
    wr(reset.va,AmethystTlmmIo,0);
    wr(reset.va,AmethystTlmmCtl,amethyst_reset_ctl);
    wr(irq.va,AmethystTlmmCtl,amethyst_irq_ctl);
    const QcomRscLayout layout=qcom_rsc_layout(rscId);
    uint32_t ioStatus=0,aStatus=0;
    int ioRc=voteRail(rsc.va,layout,iovdd.address,AmethystIovddMv,ioStatus);
    IOSleep(AmethystIovddSettleMs);
    int aRc=ioRc?-125:voteRail(rsc.va,layout,avdd.address,AmethystAvddMv,aStatus);
    IOSleep(AmethystAvddSettleMs);
    // Row 1 of at most 3 on the success path (the 48-row log is nearly full).
    snapshotLog("AmethystTouch: goodix rsc=%08x avdd=%x iovdd=%x vote io=%d/%x a=%d/%x%s\n",
        rscId,avdd.address,iovdd.address,ioRc,ioStatus,aRc,aStatus,
        (ioRc || aRc)?" STOP reset held low":"");
    if(ioRc || aRc)return 0;
    // Linux goodix_berlin_power_on order: the bus is ready before reset is
    // released; 4 ms later the boot option handshake (dev confirm), then 100 ms
    // for the firmware. V13 skipped the handshake and its event block stayed zero.
    for(unsigned i=0;i<4;++i)wr(spiPins[i].va,AmethystTlmmCtl,amethyst_spi_pins[i].ctl);
    if(amethyst_touch_dma_claim()) {
        snapshotLog("AmethystTouch: goodix stopped: buffers not available\n");
        return 0;
    }
    // The loopback qualified the engine at 0.96 MHz; the controller is rated for 15 MHz (amethyst DT
    // spi-max-frequency). 4.8 MHz from the 19.2 MHz source cuts each frame's busy wait about fivefold.
    int clockRc=amethyst_touch_source_hz?geni_spi_set_clock(spi,4800000,amethyst_touch_source_hz,0):-1;
    int rc=geni_spi_setup(spi,0,0);
    wr(reset.va,AmethystTlmmIo,2);
    IOSleep(AmethystResetSettleMs);
    static uint8_t tx[GoodixBerlinReadPrefix+GoodixBerlinIcInfoMax],rx[sizeof tx];
    uint8_t confirm[GoodixBerlinConfirmBytes],echo[GoodixBerlinConfirmBytes];
    memset(confirm,GoodixBerlinConfirmValue,sizeof confirm);memset(echo,0,sizeof echo);
    int confirmRc=rc;unsigned confirmTry=0;bool confirmed=false;
    for(;!rc && !confirmed && confirmTry<3;++confirmTry) {
        if(confirmTry)IOSleep(5);
        confirmRc=amethyst_goodix_write(spi,GoodixBerlinBootOptionAddress,confirm,sizeof confirm,tx,rx,sizeof tx);
        if(!confirmRc)confirmRc=amethyst_goodix_read(spi,GoodixBerlinBootOptionAddress,echo,sizeof echo,tx,rx,sizeof tx);
        if(confirmRc)break;
        confirmed=!memcmp(confirm,echo,sizeof echo);
    }
    IOSleep(AmethystFirmwareBootMs);
    // 500ms of the interrupt line (active low).
    unsigned low=0;
    for(unsigned i=0;i<500;++i){if(!(rd(irq.va,AmethystTlmmIo)&1))++low;IOSleep(1);}
    snapshotLog("AmethystTouch: goodix rst=%x/%x irq=%x/%x spi=%x/%x/%x/%x confirm=%d/%d try=%u rb=%02x%02x irqlow=%u/500 clk=%d/%u\n",
        rd(reset.va,AmethystTlmmCtl),rd(reset.va,AmethystTlmmIo),
        rd(irq.va,AmethystTlmmCtl),rd(irq.va,AmethystTlmmIo),
        rd(spiPins[0].va,0),rd(spiPins[1].va,0),rd(spiPins[2].va,0),rd(spiPins[3].va,0),
        confirmRc,confirmed,confirmTry,echo[0],echo[7],low,clockRc,spi->cur_speed_hz);
    uint8_t data[GoodixBerlinVersionBytes]={0};
    unsigned attempt=0;
    struct GoodixBerlinIdentity id;memset(&id,0,sizeof id);
    int parsed=0;
    // A transport error stops at once; only a bad checksum/ID is retried.
    for(;!rc && attempt<3;++attempt) {
        rc=amethyst_goodix_read(spi,GoodixBerlinVersionAddress,data,sizeof data,tx,rx,sizeof tx);
        if(rc)break;
        if((parsed=goodix_berlin_identity(data,sizeof data,&id)))break;
        IOSleep(10);
    }
    // IC_INFO: length first, then the whole block (Linux goodix_berlin_get_ic_info).
    static uint8_t info[GoodixBerlinIcInfoMax];
    struct GoodixBerlinIcInfo icInfo;memset(&icInfo,0,sizeof icInfo);
    int infoRc=-1;unsigned infoLength=0;
    if(parsed && !rc) {
        infoRc=amethyst_goodix_read(spi,GoodixBerlinIcInfoAddress,info,2,tx,rx,sizeof tx);
        infoLength=goodix_berlin_ic_info_length(info);
        if(!infoRc && (infoLength<2 || infoLength>=GoodixBerlinIcInfoMax))infoRc=-1;
        else if(!infoRc)infoRc=amethyst_goodix_read(spi,GoodixBerlinIcInfoAddress,info,infoLength,tx,rx,sizeof tx);
        if(!infoRc)infoRc=goodix_berlin_ic_info_parse(info,infoLength,&icInfo);
    }
    // goodix_htc_enable(0): the firmware boots in host touch computing mode (V13/V14
    // read an all-zero coordinate block); raw frames off, firmware coordinates on.
    unsigned events=0;
    // 1: firmware coordinates (V15/V16, no reports seen); 2: host-computing frames (V17).
    const bool wantEvents=PE_parse_boot_argn("amethyst-touch-events",&events,sizeof events) && (events==1 || events==2);
    int rawRc=-1,coordRc=-1;uint8_t rawAck=0,coordAck=0;
    if(parsed && !infoRc && icInfo.cmdAddress && wantEvents && events==1) {
        rawRc=sendCommand(spi,icInfo.cmdAddress,GoodixBerlinCmdTouchRaw,0,rawAck,tx,rx,sizeof tx);
        if(!rawRc)coordRc=sendCommand(spi,icInfo.cmdAddress,GoodixBerlinCmdTouchData,1,coordAck,tx,rx,sizeof tx);
    }
    if(parsed)
        snapshotLog("AmethystTouch: goodix id OK %u %s %02x%02x%02x %s %02x%02x%02x%02x s%u info=%d/%u t=%x c=%x raw=%d/%02x crd=%d/%02x\n",
            attempt,id.rom,id.romVersion[0],id.romVersion[1],id.romVersion[2],id.patch,
            id.patchVersion[0],id.patchVersion[1],id.patchVersion[2],id.patchVersion[3],id.sensor,
            infoRc,infoLength,icInfo.touchDataAddress,icInfo.cmdAddress,rawRc,rawAck,coordRc,coordAck);
    else
        snapshotLog("AmethystTouch: goodix id FAIL rc=%d try=%u raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
            rc,attempt,data[0],data[1],data[2],data[3],data[4],data[5],data[6],data[7],
            data[8],data[9],data[10],data[11]);
    if(spi->se.rd(spi->se.ctx,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE) {
        snapshotLog("AmethystTouch: goodix engine still active; buffers quarantined\n");
        return -16;
    }
    // Opt-in event poller: it keeps the claimed buffers and the engine, so the
    // caller must retain its mappings (nonzero return).
    if(parsed && wantEvents &&
        !amethyst_touch_events_start(spi,events,!infoRc?&icInfo:nullptr))
        return 1;
    amethyst_touch_dma_idle();
    return 0;
}
