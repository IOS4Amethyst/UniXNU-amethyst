// SPDX-License-Identifier: BSD-2-Clause
// Wi-Fi probe (scripts/bringup-24A446/WIFI-WCN6750.md) for the WPSS, the WCN6750's firmware CPU.
//
// Stage 1 (default): a read-only SMEM inventory. Wire layout from Linux drivers/soc/qcom/smem.c and smp2p.c, structure after
// chiba233/Trogdor-ipadOS coachz_wifi_smem.h (BSD-2-Clause). Only the public TOC and partitions that include the APPS host (0)
// or the global partition are read, the same ones Linux on the AP touches, so no XPU-protected pair partition of another
// processor is dereferenced. Rows 34-39: the TOC's partitions, the APPS<->WPSS (host 13) partition's items, the WPSS SMP2P
// inbound (0x269) / outbound (0x268) entries and the GLINK items (478 descriptor, 479 AP TX FIFO, 480 WPSS RX FIFO).
//
// Stage 2: boot the WPSS as Linux qcom_q6v5_pas does for qcom,volcano-wpss-pas (PAS id 6, the UEFI PIL's SubsysID 6 too). It runs
// only when the GPU addon carries the firmware (addon-trustcache.py addfiles: wpss.mdt and wpss.bNN from NON-HLOS.bin
// /image/qca6750, zlib stored blocks), so an image without it never starts anything; amethyst-wifi=1 keeps stage 1 only and
// amethyst-wifi=0 turns both off. In a kernel thread: AOSS QMP "wpss" load_state on (qcom_aoss.c, doorbell IPCC client 0
// signal 0), PAS is_supported(6), init_image(6, wpss.mdt: headers + hash segment), mem_setup(6, wpss_region 0x9bb00000,
// 0x1900000; inside UEFI's PIL_Reserved, never kernel RAM), the load segments written into the region, auth_and_reset(6),
// then up to 10 s for the WPSS's SMP2P item and GLINK RX FIFO (480). Rows 40 (step), 41 (secure monitor results), 42 (result);
// rows 34-39 are rewritten with the inventory after the wait. No RPMh cx/mx vote: the AP keeps both up while it runs.
#include <IOKit/IOLib.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <kern/task.h>
#include <kern/clock.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include <string.h>
#include "WpssIpcrtrOpen.h"

extern "C" void amethyst_touch_row_at(unsigned index,const char *format,...);
extern "C" int native_ram_block_gpu_addon(const uint8_t **data,uint64_t *bytes) __attribute__((weak_import));   // NativeRamBlock.cpp

namespace {
unsigned gRow=34;
}
// Next free inventory row (34-39).
#define snapshotLog(...) amethyst_touch_row_at(gRow++,__VA_ARGS__)

namespace {
constexpr uint64_t kSmemBase=0x81d00000ull,kSmemBytes=0x200000ull;   // stock DT smem_region@81d00000
constexpr uint32_t kApps=0,kWpss=13,kGlobal=0xfffe;
constexpr uint16_t kSmp2pIn=0x269,kSmp2pOut=0x268,kGlinkDesc=478,kGlinkTx=479,kGlinkRx=480;

struct Part { uint32_t offset,bytes,uncached,cached,cacheline; uint16_t host0,host1; };

uintptr_t gVa;
bool gStage3,gIpcrtr,gPeekIntents,gReceiveOne,gGrantRequest;
uint32_t rd32(uint32_t off) { uint32_t v; const uintptr_t a=gVa+off; __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(a) : "memory"); return v; }
uint16_t rd16(uint32_t off) { return (uint16_t)(rd32(off&~3u)>>((off&2)*8)); }
bool span(uint32_t limit,uint32_t off,uint32_t n) { return off<=limit && n<=limit-off; }

// Walks one partition's uncached (ascending) and cached (descending) item lists; calls f(item, data offset, bytes).
// Returns the item count, or -1..-4 for a malformed list (stops there).
template<class F> int walk(const Part &p,F f) {
    int n=0;
    for(uint32_t off=32;off<p.uncached;) {
        if((off&3) || !span(p.uncached,off,16))return -1;
        const uint32_t h=rd32(p.offset+off),bytes=rd32(p.offset+off+4),padgap=rd32(p.offset+off+8);
        const uint32_t pad=padgap&0xffff,gap=padgap>>16;
        if((h&0xffff)!=0xa5a5 || pad>bytes || !span(p.uncached,off+16,gap) || !span(p.uncached,off+16+gap,bytes))return -2;
        f((uint16_t)(h>>16),p.offset+off+16+gap,bytes-pad);n++;
        off+=16+gap+bytes;
    }
    const uint32_t cl=p.cacheline;
    if(cl<4 || cl>4096 || (cl&(cl-1)))return n;
    const uint32_t stride=(16+cl-1)&~(cl-1);
    if(stride>p.bytes)return -3;
    for(uint32_t off=p.bytes-stride;off>p.cached;) {
        if(off&3)return -4;
        const uint32_t h=rd32(p.offset+off),bytes=rd32(p.offset+off+4),pad=rd32(p.offset+off+8)&0xffff;
        if((h&0xffff)!=0xa5a5 || pad>bytes || bytes>off || off-bytes<p.cached)return -4;
        f((uint16_t)(h>>16),p.offset+off-bytes,bytes-pad);n++;
        if(bytes+stride>off)break;
        off-=bytes+stride;
    }
    return n;
}

// SMP2P item: "$SMP", u8 version, 3 bytes features, u16 local/remote host, u16 total/valid entries, u32 flags, then entries of
// 16-byte name + u32 value. Prints up to 6 entries as name=value.
void smp2p(const char *tag,uint32_t off,uint32_t bytes) {
    if(bytes<20 || rd32(off)!=0x504d5324u) { snapshotLog("AmethystWifi: %s bytes=%u magic=%08x\n",tag,bytes,bytes>=4?rd32(off):0);return; }
    const uint32_t vf=rd32(off+4),hosts=rd32(off+8),counts=rd32(off+12),flags=rd32(off+16);
    const unsigned total=counts&0xffff,valid=counts>>16;
    char list[96];list[0]=0;size_t used=0;
    for(unsigned i=0;i<valid && i<6 && span(bytes,20+20*i,20);i++) {
        char name[17];
        for(unsigned j=0;j<16;j+=4){uint32_t w=rd32(off+20+20*i+j);memcpy(name+j,&w,4);}
        name[16]=0;
        int k=snprintf(list+used,sizeof list-used," %.12s=%x",name,rd32(off+20+20*i+16));
        if(k<0 || (size_t)k>=sizeof list-used)break;
        used+=(size_t)k;
    }
    snapshotLog("AmethystWifi: %s v=%u feat=%x %u->%u n=%u/%u fl=%x%s\n",tag,vf&0xff,vf>>8,hosts&0xffff,hosts>>16,valid,total,flags,list);
}

// One pass over the TOC and the APPS<->WPSS / global partitions (reads only).
struct Scan {
    bool toc=false,haveWpss=false,haveGlobal=false,globalWalked=false;
    uint32_t sbl=0,avail=0,magic=0,tocver=0,count=0;
    Part wpss{},global{};
    int wpssItems=0,globalItems=0;
    int64_t in=-1,out=-1,desc=-1,tx=-1,rx=-1;
    uint32_t inBytes=0,outBytes=0,descBytes=0,txBytes=0,rxBytes=0,edgeItems=0;
    char hosts[100]{},items[100]{};
};
void scan(Scan &s) {
    // Legacy header (smem.c struct smem_header): proc_comm[4] (64 bytes), version[32] at 0x40, initialized, free_offset, available;
    // version[7] (SMEM_MASTER_SBL_VERSION_INDEX) = SMEM_GLOBAL_PART_VERSION 12 << 16 on a v12 TOC.
    s.sbl=rd32(0x40+4*7);s.avail=rd32(0x40+0x80+8);
    const uint32_t toc=(uint32_t)kSmemBytes-4096;
    s.magic=rd32(toc);s.tocver=rd32(toc+4);s.count=rd32(toc+8);
    if(s.magic!=0x434f5424u || s.tocver!=1 || s.count>(4096-32)/48)return;
    s.toc=true;
    size_t used=0;
    for(uint32_t i=0;i<s.count;i++) {
        const uint32_t e=toc+32+48*i;
        const uint32_t off=rd32(e),bytes=rd32(e+4),cacheline=rd32(e+16);
        const uint16_t h0=rd16(e+12),h1=rd16(e+14);
        const bool isGlobal=h0==kGlobal && h1==kGlobal,apps=h0==kApps || h1==kApps;
        const unsigned other=h0==kApps?h1:h0;
        if(!off && !bytes)continue;
        int k=snprintf(s.hosts+used,sizeof s.hosts-used," %x-%x",h0,h1);
        if(k>0 && (size_t)k<sizeof s.hosts-used)used+=(size_t)k;
        if(!isGlobal && !apps)continue;   // another processor pair: never read
        if(!off || bytes<32 || (off|bytes)&3 || !span(toc,off,bytes) || cacheline<4 || cacheline>4096 || (cacheline&(cacheline-1)))continue;
        if(rd32(off)!=0x54525024u || rd16(off+4)!=h0 || rd16(off+6)!=h1 || rd32(off+8)!=bytes)continue;
        Part p{off,bytes,rd32(off+12),rd32(off+16),cacheline,h0,h1};
        if(p.uncached<32 || p.uncached>p.cached || p.cached>bytes)continue;
        if(isGlobal){s.global=p;s.haveGlobal=true;}
        else if(other==kWpss){s.wpss=p;s.haveWpss=true;}
    }
    if(s.haveWpss) {
        size_t at=0;
        s.wpssItems=walk(s.wpss,[&](uint16_t item,uint32_t off,uint32_t bytes){
            if(item==kSmp2pIn){s.in=off;s.inBytes=bytes;}
            if(item==kSmp2pOut){s.out=off;s.outBytes=bytes;}
            if(item==kGlinkDesc){s.desc=off;s.descBytes=bytes;++s.edgeItems;}
            if(item==kGlinkTx){s.tx=off;s.txBytes=bytes;++s.edgeItems;}
            if(item==kGlinkRx){s.rx=off;s.rxBytes=bytes;++s.edgeItems;}
            int k=snprintf(s.items+at,sizeof s.items-at," %x:%u",item,bytes);
            if(k>0 && (size_t)k<sizeof s.items-at)at+=(size_t)k;
        });
    }
    // smem.c falls back to the global partition for items not in the pair partition.
    if(s.haveGlobal && (s.in<0 || s.out<0) && (s.globalWalked=true))
        s.globalItems=walk(s.global,[&](uint16_t item,uint32_t off,uint32_t bytes){
            if(item==kSmp2pIn && s.in<0){s.in=off;s.inBytes=bytes;}
            if(item==kSmp2pOut && s.out<0){s.out=off;s.outBytes=bytes;}
        });
}
void inventory() {
    Scan s;scan(s);
    if(!s.toc) { snapshotLog("AmethystWifi: smem sbl=%x avail=%x toc=%08x v=%u n=%u (no v12 TOC)\n",s.sbl,s.avail,s.magic,s.tocver,s.count);return; }
    snapshotLog("AmethystWifi: smem sbl=%x toc n=%u parts%s\n",s.sbl,s.count,s.hosts);
    if(s.haveWpss)
        snapshotLog("AmethystWifi: wpss part @%x+%x unc=%x cac=%x cl=%u items=%d%s\n",s.wpss.offset,s.wpss.bytes,s.wpss.uncached,s.wpss.cached,
            s.wpss.cacheline,s.wpssItems,s.items);
    else snapshotLog("AmethystWifi: no APPS-WPSS partition (global=%d)\n",s.haveGlobal);
    if(s.globalWalked)
        snapshotLog("AmethystWifi: global @%x+%x items=%d in=%d out=%d\n",s.global.offset,s.global.bytes,s.globalItems,s.in>=0,s.out>=0);
    if(s.in>=0)smp2p("smp2p-in",(uint32_t)s.in,s.inBytes); else snapshotLog("AmethystWifi: smp2p-in 0x269 absent\n");
    if(s.out>=0)smp2p("smp2p-out",(uint32_t)s.out,s.outBytes); else snapshotLog("AmethystWifi: smp2p-out 0x268 absent\n");
    snapshotLog("AmethystWifi: glink desc=%d tx=%d rx=%d\n",s.desc>=0,s.tx>=0,s.rx>=0);
}

// ---- stage 2 ----
#define stepLog(...) amethyst_touch_row_at(40,__VA_ARGS__)
#define scmLog(...) amethyst_touch_row_at(41,__VA_ARGS__)
#define resultLog(...) amethyst_touch_row_at(42,__VA_ARGS__)

constexpr uint32_t kPas=6;
constexpr uint64_t kRegion=0x9bb00000ull,kRegionBytes=0x1900000ull;   // stock DT wpss_region / remoteproc-wpss memory-region
constexpr uint64_t kQmp=0x0c300000ull,kQmpBytes=0x400;                // power-controller@c300000 (qcom,aoss-qmp) message RAM
constexpr uint64_t kIpcc=0x00405000ull;                                // qcom,ipcc@405000; mboxes = <ipcc 0 0> for the AOSS
constexpr unsigned kMaxSegments=16;

// The addon's files (addon-trustcache.py): "NGPUADD2", u32 count, u32 0, u64 total; per file u32 path bytes (with NUL), u32 mode,
// u64 size, u64 packed size, the path and the zlib data, each padded to 8.
struct AddonFile { const uint8_t *z; uint64_t packed,size; };
bool addonFile(const uint8_t *blob,uint64_t total,const char *name,AddonFile &f) {
    if(total<24 || memcmp(blob,"NGPUADD2",8))return false;
    uint32_t count;uint64_t declared;memcpy(&count,blob+8,4);memcpy(&declared,blob+16,8);
    if(declared!=total)return false;
    uint64_t at=24;
    for(uint32_t i=0;i<count;i++) {
        if(total-at<24)return false;
        uint32_t nameLen;uint64_t size,packed;memcpy(&nameLen,blob+at,4);memcpy(&size,blob+at+8,8);memcpy(&packed,blob+at+16,8);
        at+=24;
        const uint64_t namePad=(nameLen+7ull)&~7ull,dataPad=(packed+7ull)&~7ull;
        if(!nameLen || namePad>total-at || dataPad>total-at-namePad)return false;
        const bool match=strnlen((const char *)blob+at,nameLen)==nameLen-1 && !strcmp((const char *)blob+at,name);
        at+=namePad;
        if(match){f={blob+at,packed,size};return true;}
        at+=dataPad;
    }
    return false;
}
// Copies a zlib stream of stored blocks (zlib level 0) to dst; false for anything else or a size mismatch.
template<class Out> bool unstore(const AddonFile &f,Out out) {
    const uint8_t *z=f.z;uint64_t n=f.packed;
    if(n<2 || (z[0]&0x0f)!=8 || ((z[0]<<8)|z[1])%31 || (z[1]&0x20))return false;
    uint64_t at=2,done=0;
    for(;;) {
        if(n-at<5)return false;
        const uint8_t h=z[at];
        if(h&~1u)return false;   // BTYPE 00, byte aligned
        const uint32_t len=z[at+1]|z[at+2]<<8,nlen=z[at+3]|z[at+4]<<8;
        if((len^nlen)!=0xffff || n-at-5<len || f.size-done<len)return false;
        out(done,z+at+5,len);
        done+=len;at+=5+len;
        if(h&1)break;
    }
    return done==f.size;
}

// Secure monitor calls as Qualcomm's monitor takes them (GpuPowerBoard.h in UniXNU-amethyst): a yielding SMC64 SiP call,
// function 0x42000000 | svc << 8 | cmd, x1 the argument description, arguments from x2; x0 = 1 (interrupted) asks for the
// call again with x0 = 1 and the returned x6. Status in x0, first result in x1.
struct Scm { uint64_t status,value; };
Scm scm(uint32_t svc,uint32_t cmd,uint32_t arginfo,uint64_t a0,uint64_t a1,uint64_t a2) {
    uint64_t r0=0x42000000u|svc<<8|cmd,r1=arginfo,r2=a0,r3=a1,r4=a2,r6=0;
    for(unsigned again=0;;again++) {
        register uint64_t x0 __asm__("x0")=r0,x1 __asm__("x1")=r1,x2 __asm__("x2")=r2,x3 __asm__("x3")=r3,x4 __asm__("x4")=r4,
            x5 __asm__("x5")=0,x6 __asm__("x6")=r6,x7 __asm__("x7")=0;
        __asm__ volatile("smc #0" : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3),"+r"(x4),"+r"(x5),"+r"(x6),"+r"(x7) :
            : "memory","x8","x9","x10","x11","x12","x13","x14","x15","x16","x17");
        if(x0!=1 || again>=1000)return {x0,x1};
        r0=1;r6=x6;
    }
}

void cleanToMemory(const void *p,size_t n) {
    uint64_t ctr;__asm__ volatile("mrs %0, ctr_el0":"=r"(ctr));
    const uintptr_t line=4ull<<((ctr>>16)&15);
    uintptr_t at=reinterpret_cast<uintptr_t>(p)&~(line-1);const uintptr_t end=reinterpret_cast<uintptr_t>(p)+n;
    __asm__ volatile("dsb sy":::"memory");
    for(;at<end;at+=line)__asm__ volatile("dc civac, %0"::"r"(at):"memory");
    __asm__ volatile("dsb sy":::"memory");
}

IOMemoryMap *mapDevice(uint64_t pa,uint64_t bytes,IOOptionBits cache) {
    IODeviceMemory *m=IODeviceMemory::withRange(pa,bytes);
    IOMemoryMap *map=m?m->map(kIOMapAnywhere|cache):nullptr;
    if(m)m->release();
    return map;
}

// AOSS QMP (Linux drivers/soc/qcom/qcom_aoss.c). Returns 0 or the failing step.
int qmpLoadState(uint32_t &states) {
    IOMemoryMap *qm=mapDevice(kQmp,kQmpBytes,kIOMapInhibitCache),*im=mapDevice(kIpcc,0x1000,kIOMapInhibitCache);
    int rc=0;
    if(!qm || !im){rc=1;}
    else {
        volatile uint32_t *q=reinterpret_cast<volatile uint32_t *>(qm->getVirtualAddress());
        volatile uint32_t *ipcc=reinterpret_cast<volatile uint32_t *>(im->getVirtualAddress());
        auto kick=[&]{__asm__ volatile("dsb sy":::"memory");ipcc[0x0c/4]=(0u<<16)|0u;};   // IPCC_REG_SEND_ID: client AOP 0, signal 0
        auto wait=[&](unsigned word,uint32_t want){for(unsigned i=0;i<1000;i++){if(q[word/4]==want)return true;IODelay(1000);}return false;};
        constexpr uint32_t up=0x0000ffffu;
        states=(q[0x24/4]==up)|(q[0x28/4]==up)<<1|(q[0x2c/4]==up)<<2|(q[0x30/4]==up)<<3|(q[0x14/4]==up)<<4;
        if(q[0]!=0x4d41494cu || q[1]!=1)rc=2;
        else {
            const uint32_t size=q[0x34/4],offset=q[0x38/4];
            if(size<68 || offset>kQmpBytes || size>kQmpBytes-offset)rc=3;
            else if(q[0x30/4]!=up) {   // channel not open yet (UEFI leaves it closed or never used it): qmp_open
                q[0x24/4]=up;kick();
                if(!wait(0x28,up))rc=4;
                else { q[0x2c/4]=up;kick();
                    if(!wait(0x14,up))rc=5;
                    else { q[0x18/4]=up;kick(); if(!wait(0x30,up))rc=6; } }
            }
            if(!rc) {
                char msg[64];memset(msg,0,sizeof msg);
                snprintf(msg,sizeof msg,"{class: image, res: load_state, name: wpss, val: on}");
                if(q[offset/4]){rc=7;}
                else {
                    for(unsigned i=0;i<16;i++){uint32_t w;memcpy(&w,msg+4*i,4);q[offset/4+1+i]=w;}
                    __asm__ volatile("dsb sy":::"memory");
                    q[offset/4]=sizeof msg;   // the length goes last
                    kick();
                    if(!wait(offset,0)){q[offset/4]=0;rc=8;}
                }
            }
        }
    }
    if(qm)qm->release();
    if(im)im->release();
    return rc;
}

bool gBootRan;
void stage2(void *,int) {
    const uint64_t start=mach_absolute_time();
    auto ms=[&]{uint64_t ns;absolutetime_to_nanoseconds(mach_absolute_time()-start,&ns);return (unsigned)(ns/1000000);};
    const uint8_t *blob=nullptr;uint64_t total=0;
    native_ram_block_gpu_addon(&blob,&total);
    AddonFile mdt{};
    if(!blob || !addonFile(blob,total,"wpss.mdt",mdt) || mdt.size<0x34 || mdt.size>0x2000) {
        resultLog("AmethystWifi: s2 wpss.mdt %s\n",blob?"bad":"no addon");return;
    }
    // wpss.mdt: ELF32 headers then the hash segment (qcom_mdt_read_metadata's split form), i.e. the PAS metadata as is.
    // b39: a 16 KiB contiguous buffer under 4 GiB was refused. The touch DMA buffer's form (40-bit mask) is tried after the 32-bit one,
    // 8 KiB (wpss.mdt is 4844 bytes); each refusal is counted in the row.
    constexpr uint64_t kMetaBytes=0x2000;
    IOBufferMemoryDescriptor *meta=nullptr;unsigned refused=0;
    static const uint64_t kMasks[]={0xfffff000ull,0xfffffff000ull};
    for(const uint64_t mask:kMasks) {
        meta=IOBufferMemoryDescriptor::inTaskWithPhysicalMask(kernel_task,
            static_cast<IOOptionBits>(kIODirectionInOut)|static_cast<IOOptionBits>(kIOMemoryPhysicallyContiguous),kMetaBytes,mask);
        if(meta && meta->prepare()==kIOReturnSuccess)break;
        refused=refused*10+(meta?2:1);
        if(meta){meta->release();meta=nullptr;}
    }
    if(!meta){resultLog("AmethystWifi: s2 metadata buffer refused %u\n",refused);return;}
    uint8_t *mv=static_cast<uint8_t *>(meta->getBytesNoCopy());
    IOByteCount length=0;const uint64_t mpa=meta->getPhysicalSegment(0,&length,kIOMemoryMapperNone);
    if(!mv || !mpa || length<mdt.size){resultLog("AmethystWifi: s2 metadata buffer has no address (%llx+%llx)\n",mpa,length);return;}
    memset(mv,0,kMetaBytes);
    if(!unstore(mdt,[&](uint64_t at,const uint8_t *p,uint32_t n){memcpy(mv+at,p,n);})){resultLog("AmethystWifi: s2 wpss.mdt not stored zlib\n");return;}
    // Program headers: load segments (PT_LOAD, memsz != 0, not the hash segment) relative to the lowest address.
    const uint8_t *e=mv;
    uint32_t phoff,ph[8];uint16_t phent,phnum;memcpy(&phoff,e+0x1c,4);memcpy(&phent,e+0x2a,2);memcpy(&phnum,e+0x2c,2);
    if(memcmp(e,"\x7f" "ELF",4) || e[4]!=1 || phent!=32 || !phnum || phnum>kMaxSegments || phoff+32u*phnum>mdt.size){resultLog("AmethystWifi: s2 wpss.mdt not ELF32\n");return;}
    uint32_t lo=~0u,hi=0;bool reloc=false;
    for(unsigned i=0;i<phnum;i++) {
        memcpy(ph,e+phoff+32*i,32);
        if(ph[0]!=1 || !ph[5] || ((ph[6]>>24)&7)==2)continue;
        if(ph[4]>ph[5]){resultLog("AmethystWifi: s2 segment %u filesz>memsz\n",i);return;}
        if(ph[6]&(1u<<27))reloc=true;
        if(ph[3]<lo)lo=ph[3];
        if(ph[3]+ph[5]>hi)hi=ph[3]+ph[5];
    }
    if(lo==~0u || hi-lo>kRegionBytes){resultLog("AmethystWifi: s2 span %x-%x over region\n",lo,hi);return;}
    // Every segment file present before anything starts.
    AddonFile seg[kMaxSegments]{};
    for(unsigned i=0;i<phnum;i++) {
        memcpy(ph,e+phoff+32*i,32);
        if(ph[0]!=1 || !ph[5] || ((ph[6]>>24)&7)==2 || !ph[4])continue;
        char name[16];snprintf(name,sizeof name,"wpss.b%02u",i);
        if(!addonFile(blob,total,name,seg[i]) || seg[i].size!=ph[4]){resultLog("AmethystWifi: s2 %s missing or %llu!=%u\n",name,seg[i].size,ph[4]);return;}
    }
    stepLog("AmethystWifi: s2 mdt=%llu segs span=%x-%x reloc=%d meta@%llx\n",mdt.size,lo,hi,reloc,mpa);
    cleanToMemory(mv,mdt.size);

    uint32_t qs=0;const int q=qmpLoadState(qs);
    stepLog("AmethystWifi: s2 qmp load_state rc=%d states=%x t=%u\n",q,qs,ms());
    Scm avail=scm(0x06,0x01,0x01,0x02000000u|0x02<<8|0x07,0,0);       // INFO is_call_avail(PIL PAS_IS_SUPPORTED)
    Scm sup=avail.status||!avail.value?Scm{~0ull,0}:scm(0x02,0x07,0x01,kPas,0,0);
    scmLog("AmethystWifi: s2 scm avail=%llx/%llx sup=%llx/%llx\n",avail.status,avail.value,sup.status,sup.value);
    if(sup.status || !sup.value){resultLog("AmethystWifi: s2 PAS %u unsupported\n",kPas);return;}
    Scm init=scm(0x02,0x01,0x82,kPas,mpa,0);                          // PAS_INIT_IMAGE(id, metadata)
    Scm setup=init.status||init.value?Scm{~0ull,0}:scm(0x02,0x02,0x03,kPas,kRegion,hi-lo);   // PAS_MEM_SETUP(id, region, span)
    scmLog("AmethystWifi: s2 scm sup=%llx init=%llx/%llx setup=%llx/%llx\n",sup.value,init.status,init.value,setup.status,setup.value);
    if(init.status || init.value || setup.status || setup.value){resultLog("AmethystWifi: s2 init/setup refused t=%u\n",ms());return;}
    stepLog("AmethystWifi: s2 writing segments t=%u\n",ms());
    IOMemoryMap *rm=mapDevice(kRegion,kRegionBytes,kIOMapWriteCombineCache);
    if(!rm){resultLog("AmethystWifi: s2 region map failed\n");return;}
    volatile uint8_t *region=reinterpret_cast<volatile uint8_t *>(rm->getVirtualAddress());
    uint64_t written=0;
    for(unsigned i=0;i<phnum;i++) {
        memcpy(ph,e+phoff+32*i,32);
        if(ph[0]!=1 || !ph[5] || ((ph[6]>>24)&7)==2)continue;
        volatile uint8_t *to=region+(ph[3]-lo);
        if(ph[4] && !unstore(seg[i],[&](uint64_t at,const uint8_t *p,uint32_t n){for(uint32_t k=0;k<n;k++)to[at+k]=p[k];})){
            rm->release();resultLog("AmethystWifi: s2 wpss.b%02u not stored zlib\n",i);return;}
        for(uint32_t k=ph[4];k<ph[5];k++)to[k]=0;
        written+=ph[5];
    }
    __asm__ volatile("dsb sy":::"memory");
    rm->release();
    stepLog("AmethystWifi: s2 wrote %llu bytes t=%u, auth_and_reset\n",written,ms());
    Scm auth=scm(0x02,0x05,0x01,kPas,0,0);                            // PAS_AUTH_AND_RESET(id)
    scmLog("AmethystWifi: s2 scm init=%llx setup=%llx auth=%llx/%llx t=%u\n",init.status,setup.status,auth.status,auth.value,ms());
    if(auth.status || auth.value){resultLog("AmethystWifi: s2 auth_and_reset refused %llx/%llx\n",auth.status,auth.value);return;}
    // The metadata stays allocated: the monitor may still read it.
    Scan s;
    unsigned waited=0;
    for(;waited<10000;waited+=100) {
        scan(s);
        if(s.in>=0 && s.rx>=0)break;
        IOSleep(100);
        s=Scan();
    }
    uint32_t bits=0;
    if(s.in>=0 && s.inBytes>=40 && rd32((uint32_t)s.in)==0x504d5324u)bits=rd32((uint32_t)s.in+20+16);   // first entry's value
    resultLog("AmethystWifi: s2 WPSS %s after %u ms: smp2p-in=%d entry0=%x glink rx=%d\n",s.in>=0&&s.rx>=0?"UP":"no answer",
        waited,s.in>=0,bits,s.rx>=0);
    gRow=34;inventory();
    if(!gStage3)return;
    if(s.wpssItems<0 || s.edgeItems!=3 || s.desc<0 || s.tx<0 || s.rx<0 ||
       (s.desc|s.tx|s.rx)&3 || s.descBytes!=32){resultLog("AmethystWifi: s3 invalid edge\n");return;}
    IOMemoryMap *im=mapDevice(kIpcc,0x1000,kIOMapInhibitCache);
    if(!im){resultLog("AmethystWifi: s3 IPCC map failed\n");return;}
    auto word=[](int64_t off){return reinterpret_cast<volatile uint32_t *>(gVa+off);};
    WpssVersion link{};link.d=word(s.desc);link.tx=word(s.tx);link.rx=word(s.rx);
    link.tn=s.txBytes;link.rn=s.rxBytes;
    auto sendID=reinterpret_cast<volatile uint32_t *>(im->getVirtualAddress()+0x0c);
    auto kick=[&]{*sendID=0x18u<<16;};
    int rc=0;
    if(gIpcrtr) {
        WpssIpcrtrOpen channel(link);
        for(unsigned i=0;i<500 && !rc;++i){rc=channel.step(kick);if(!rc)IOSleep(10);}
        if(channel.openHeader) {
            const auto &n=channel.openName;
            stepLog("AmethystWifi: s4 OPEN hdr=%08x arg=%08x n=%u prefix=%02x%02x%02x%02x%02x%02x%02x ignored=%u\n",
                channel.openHeader,channel.openArg,(unsigned)channel.openNameBytes,
                (unsigned)n[0],(unsigned)n[1],(unsigned)n[2],(unsigned)n[3],(unsigned)n[4],(unsigned)n[5],(unsigned)n[6],channel.ignoredOpens);
        }
        resultLog("AmethystWifi: s4 IPCRTR rc=%d v=%u/%u/%u peer=%u ack=%u open=%u last=%u\n",
            rc,(unsigned)link.sent,(unsigned)link.replied,(unsigned)link.agreed,
            (unsigned)channel.rcid,(unsigned)channel.ackSent,(unsigned)channel.openSent,(unsigned)channel.last);
        if(gPeekIntents && rc==1) {
            uint32_t count=0,firstId=0,largest=0;
            int observed=0;
            for(unsigned i=0;i<100 && !observed;++i){observed=channel.peekIntents(count,firstId,largest);if(!observed)IOSleep(10);}
            scmLog("AmethystWifi: s5 intents rc=%d count=%u first=%u max=%u\n",observed,count,firstId,largest);
        }
        if(gGrantRequest && rc==1) {
            channel.grantRequests=true;
            int received=0;
            for(unsigned i=0;i<200 && !received;++i){received=channel.receiveOne(kick);if(!received)IOSleep(10);}
            const uint8_t *p=channel.payload;
            const uint32_t w0=p[0]|p[1]<<8|p[2]<<16|uint32_t(p[3])<<24,w1=p[4]|p[5]<<8|p[6]<<16|uint32_t(p[7])<<24;
            scmLog("AmethystWifi: s7 rc=%d req=%u/%u g=%u liid=%u bytes=%u cmd=%u rn=%u q=%08x/%08x\n",
                received,channel.requestedBytes,channel.requests,(unsigned)channel.granted,channel.receivedId,
                channel.receivedBytes,channel.nextCommand,channel.notifies,w0,w1);
        }
        if(gReceiveOne && rc==1) {
            int received=0;
            for(unsigned i=0;i<100 && !received;++i){received=channel.receiveOne(kick);if(!received)IOSleep(10);}
            scmLog("AmethystWifi: s6 receive rc=%d advertised=%u bytes=%u cmd=%u\n",
                received,(unsigned)channel.advertised,channel.receivedBytes,channel.nextCommand);
        }
    } else {
        for(unsigned i=0;i<500 && !rc;++i){rc=link.step(kick);if(!rc)IOSleep(10);}
        resultLog("AmethystWifi: s3 version rc=%d sent=%u replied=%u agreed=%u\n",rc,link.sent,link.replied,link.agreed);
    }
    im->release();
}
}

extern "C" void amethyst_wifi_probe(void);
extern "C" void amethyst_wifi_probe(void) {
    static bool done=false;
    if(done)return;
    done=true;
    unsigned mode=2;PE_parse_boot_argn("amethyst-wifi",&mode,sizeof mode);
    if(!mode)return;
    gPeekIntents=(mode==5);
    gReceiveOne=(mode==6);
    gGrantRequest=(mode==7);
    gIpcrtr=(mode==4 || gPeekIntents || gReceiveOne || gGrantRequest);
    gStage3=(mode==3 || gIpcrtr);
    IOMemoryMap *map=mapDevice(kSmemBase,kSmemBytes,kIOMapInhibitCache|(gStage3?0:kIOMapReadOnly));
    if(!map) { snapshotLog("AmethystWifi: smem map failed\n");return; }
    gVa=(uintptr_t)map->getVirtualAddress();   // kept mapped for stage 2
    inventory();
    if(mode<2 || gBootRan)return;
    const uint8_t *blob=nullptr;uint64_t total=0;AddonFile f{};
    if(!native_ram_block_gpu_addon || !native_ram_block_gpu_addon(&blob,&total) || !addonFile(blob,total,"wpss.mdt",f))return;   // no firmware: stage 1 only
    gBootRan=true;
    thread_t thread=nullptr;
    if(kernel_thread_start(stage2,nullptr,&thread)==KERN_SUCCESS)thread_deallocate(thread);
    else resultLog("AmethystWifi: s2 no thread\n");
}
