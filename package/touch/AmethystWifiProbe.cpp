// SPDX-License-Identifier: BSD-2-Clause
// Wi-Fi stage 1 (scripts/bringup-24A446/WIFI-WCN6750.md): read-only SMEM inventory for the WPSS (WCN6750 firmware CPU).
// Wire layout from Linux drivers/soc/qcom/smem.c and smp2p.c, structure after chiba233/Trogdor-ipadOS coachz_wifi_smem.h
// (BSD-2-Clause). Nothing is written: no allocation, no hwspinlock, no SMP2P/IPCC signal. Only the public TOC and partitions
// that include the APPS host (0) or the global partition are read, the same ones Linux on the AP touches, so no XPU-protected
// pair partition of another processor is dereferenced.
//
// Rows 34-42 (amethyst_touch_row_at, once): the TOC's partitions, the APPS<->WPSS (host 13) partition's items, and the WPSS SMP2P inbound
// (0x269) / outbound (0x268) entries plus the GLINK descriptor (478). Stock Android boots WPSS from the kernel, so before stage 2
// the expected result is a WPSS partition with no SMP2P/GLINK items. Default on (read-only); amethyst-wifi=0 turns it off.
#include <IOKit/IOLib.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <pexpert/pexpert.h>
#include <stdint.h>
#include <string.h>

extern "C" void amethyst_touch_row_at(unsigned index,const char *format,...);

namespace {
unsigned gRow=34;
}
// Next free probe row (34-42); later lines past 42 are dropped.
#define snapshotLog(...) amethyst_touch_row_at(gRow++,__VA_ARGS__)

namespace {
constexpr uint64_t kSmemBase=0x81d00000ull,kSmemBytes=0x200000ull;   // stock DT smem_region@81d00000
constexpr uint32_t kApps=0,kWpss=13,kGlobal=0xfffe;
constexpr uint16_t kSmp2pIn=0x269,kSmp2pOut=0x268,kGlinkDesc=478;

struct Part { uint32_t offset,bytes,uncached,cached,cacheline; uint16_t host0,host1; };

uintptr_t gVa;
uint32_t rd32(uint32_t off) { uint32_t v; const uintptr_t a=gVa+off; __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(a) : "memory"); return v; }
uint16_t rd16(uint32_t off) { return (uint16_t)(rd32(off&~3u)>>((off&2)*8)); }
bool span(uint32_t limit,uint32_t off,uint32_t n) { return off<=limit && n<=limit-off; }

// Walks one partition's uncached (ascending) and cached (descending) item lists; calls f(item, data offset, bytes).
// Returns the item count, or -1..-4 for a malformed list (stops there).
template<class F> int walk(const Part &p,F f) {
    int n=0;
    for(uint32_t off=32;off<p.uncached;) {
        if(!span(p.uncached,off,16))return -1;
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
}

extern "C" void amethyst_wifi_probe(void);
extern "C" void amethyst_wifi_probe(void) {
    static bool done=false;
    if(done)return;
    done=true;
    unsigned mode=1;PE_parse_boot_argn("amethyst-wifi",&mode,sizeof mode);
    if(!mode)return;
    IODeviceMemory *memory=IODeviceMemory::withRange(kSmemBase,kSmemBytes);
    IOMemoryMap *map=memory?memory->map(kIOMapAnywhere|kIOMapInhibitCache|kIOMapReadOnly):nullptr;
    if(memory)memory->release();
    if(!map) { snapshotLog("AmethystWifi: smem map failed\n");return; }
    gVa=(uintptr_t)map->getVirtualAddress();
    // Legacy header (smem.c struct smem_header): proc_comm[4] (64 bytes), version[32] at 0x40, initialized, free_offset, available;
    // version[7] (SMEM_MASTER_SBL_VERSION_INDEX) = SMEM_GLOBAL_PART_VERSION 12 << 16 on a v12 TOC.
    const uint32_t sbl=rd32(0x40+4*7),avail=rd32(0x40+0x80+8);
    const uint32_t toc=(uint32_t)kSmemBytes-4096;
    const uint32_t magic=rd32(toc),tocver=rd32(toc+4),count=rd32(toc+8);
    if(magic!=0x434f5424u || tocver!=1 || count>(4096-32)/48) {
        snapshotLog("AmethystWifi: smem sbl=%x avail=%x toc=%08x v=%u n=%u (no v12 TOC)\n",sbl,avail,magic,tocver,count);
        map->release();return;
    }
    char hosts[100];hosts[0]=0;size_t used=0;
    Part wpss{},global{};bool haveWpss=false,haveGlobal=false;
    for(uint32_t i=0;i<count;i++) {
        const uint32_t e=toc+32+48*i;
        const uint32_t off=rd32(e),bytes=rd32(e+4),cacheline=rd32(e+16);
        const uint16_t h0=rd16(e+12),h1=rd16(e+14);
        const bool isGlobal=h0==kGlobal && h1==kGlobal,apps=h0==kApps || h1==kApps;
        const unsigned other=h0==kApps?h1:h0;
        if(!off && !bytes)continue;
        int k=snprintf(hosts+used,sizeof hosts-used," %x-%x",h0,h1);
        if(k>0 && (size_t)k<sizeof hosts-used)used+=(size_t)k;
        if(!isGlobal && !apps)continue;   // another processor pair: never read
        if(!off || bytes<32 || (off|bytes)&3 || !span(toc,off,bytes) || cacheline<4 || cacheline>4096 || (cacheline&(cacheline-1)))continue;
        if(rd32(off)!=0x54525024u || rd16(off+4)!=h0 || rd16(off+6)!=h1 || rd32(off+8)!=bytes)continue;
        Part p{off,bytes,rd32(off+12),rd32(off+16),cacheline,h0,h1};
        if(p.uncached<32 || p.uncached>p.cached || p.cached>bytes)continue;
        if(isGlobal){global=p;haveGlobal=true;}
        else if(other==kWpss){wpss=p;haveWpss=true;}
    }
    snapshotLog("AmethystWifi: smem sbl=%x toc n=%u parts%s\n",sbl,count,hosts);
    int64_t in=-1,out=-1,desc=-1;uint32_t inBytes=0,outBytes=0;
    if(haveWpss) {
        char items[100];items[0]=0;size_t at=0;
        int n=walk(wpss,[&](uint16_t item,uint32_t off,uint32_t bytes){
            if(item==kSmp2pIn){in=off;inBytes=bytes;}
            if(item==kSmp2pOut){out=off;outBytes=bytes;}
            if(item==kGlinkDesc)desc=off;
            int k=snprintf(items+at,sizeof items-at," %x:%u",item,bytes);
            if(k>0 && (size_t)k<sizeof items-at)at+=(size_t)k;
        });
        snapshotLog("AmethystWifi: wpss part @%x+%x unc=%x cac=%x cl=%u items=%d%s\n",wpss.offset,wpss.bytes,wpss.uncached,wpss.cached,
            wpss.cacheline,n,items);
    } else snapshotLog("AmethystWifi: no APPS-WPSS partition (global=%d)\n",haveGlobal);
    // smem.c falls back to the global partition for items not in the pair partition.
    if(haveGlobal && (in<0 || out<0)) {
        int n=walk(global,[&](uint16_t item,uint32_t off,uint32_t bytes){
            if(item==kSmp2pIn && in<0){in=off;inBytes=bytes;}
            if(item==kSmp2pOut && out<0){out=off;outBytes=bytes;}
        });
        snapshotLog("AmethystWifi: global @%x+%x items=%d in=%d out=%d\n",global.offset,global.bytes,n,in>=0,out>=0);
    }
    if(in>=0)smp2p("smp2p-in",(uint32_t)in,inBytes); else snapshotLog("AmethystWifi: smp2p-in 0x269 absent\n");
    if(out>=0)smp2p("smp2p-out",(uint32_t)out,outBytes); else snapshotLog("AmethystWifi: smp2p-out 0x268 absent\n");
    snapshotLog("AmethystWifi: glink desc=%s\n",desc>=0?"present":"absent");
    map->release();
}
