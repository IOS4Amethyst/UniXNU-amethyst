// SPDX-License-Identifier: BSD-2-Clause
#pragma once
#include "WpssGlinkVersion.h"

// One peer-initiated IPCRTR handshake, following Asteroids/Sheng QcomGlink.
// ponytail: no intents, CLOSE, restart or QRTR; replace with a live endpoint when adding those.
struct WpssIpcrtrOpen {
    WpssVersion &link;
    explicit WpssIpcrtrOpen(WpssVersion &version):link(version) {}
    uint16_t rcid=0, last=0xffff;
    uint32_t openHeader=0, openArg=0;
    uint8_t openName[7]={}, openNameBytes=0;
    bool ackSent=false, openSent=false, openAcked=false;

    // 0 pending, 1 OPEN in both directions, -1 bad ring, -2 unsupported command, -3 invalid OPEN/ACK.
    template<class Kick> int step(Kick kick) {
        if(openAcked)return 1;
        auto d=link.d;
        const uint32_t tt=d[0],th=d[1],rt=d[2],rh=d[3],tn=link.tn,rn=link.rn;
        if(tn<32 || rn<24 || ((tn|rn|tt|th|rt|rh)&7) || tt>=tn || th>=tn || rt>=rn || rh>=rn)return -1;
        WpssVersion::fence();
        const bool versionDone=link.replied && link.agreed;
        const bool empty=rt==rh;
        const uint32_t hdr=empty?0:link.rx[rt/4];
        if(!empty)last=hdr&0xffff;
        if(!versionDone && (empty || last<=1)) {
            if(empty && link.sent)return 0;
            const int rc=link.step(kick);
            // A peer frame can arrive after our empty snapshot. The version probe leaves it queued.
            return rc<0 && !(empty && rc==-2)?rc:0;
        }

        if(!empty) {
            const uint16_t cid=hdr>>16;
            const uint32_t arg=link.rx[rt/4+1];
            uint32_t size=8;
            switch(last) {
            case 2: { // OPEN: low 16 bits are length; high bits can carry priority.
                // Record only published bytes, capped at the expected name size.
                const uint32_t available=rh>rt?rh-rt:rn-rt+rh;
                openHeader=hdr;openArg=arg;openNameBytes=0;
                for(auto &byte:openName)byte=0;
                for(uint32_t i=0;i<sizeof openName && i<(arg&0xffff) && i+8<available;++i) {
                    const uint32_t at=advance(rt,8+i,rn);
                    openName[openNameBytes++]=uint8_t(link.rx[at/4]>>((at&3)*8));
                }
                if(!cid || (arg&0xffff)!=7 || (rcid && rcid!=cid))return -3;
                if(available<16)return 0;
                if(link.rx[advance(rt,8,rn)/4]!=name0 ||
                   (link.rx[advance(rt,8,rn)/4+1]&0xffffff)!=name1)return -3;
                rcid=cid; // Stash even before VERSION_ACK, so it cannot block version frames behind it.
                size=16;
                break;
            }
            case 4: // OPEN_ACK echoes our local, not the remote, CID.
                if(!openSent || cid!=localCid || arg)return -3;
                openAcked=true;
                break;
            case 13: // READ_NOTIF: ring the peer once the tail is visible.
                if(cid || arg)return -3;
                break;
            default:
                return -2; // Leave unsupported frames queued; never guess their size.
            }
            WpssVersion::fence();
            d[2]=advance(rt,size,rn);
            WpssVersion::fence();
            if(last==13)kick();
        }

        if(versionDone && rcid && !openSent) {
            // Linux SMEM reserves eight bytes each for the full/empty gap and READ_NOTIF.
            const uint32_t free=tt>th?tt-th:tn-th+tt;
            const uint32_t size=ackSent?16:8;
            if(free>=size+16) {
                if(!ackSent) {
                    publish(th,4u|(uint32_t(rcid)<<16),0,8,kick);
                    ackSent=true;
                } else {
                    publish(th,2u|(localCid<<16),7,16,kick);
                    openSent=true;
                }
                notified=false;
            } else if(!notified && free>=16) {
                publish(th,13,0,8,kick);
                notified=true;
            }
        }
        return openAcked;
    }

private:
    static constexpr uint32_t localCid=1,name0=0x52435049,name1=0x005254; // "IPCRTR\0" as LE words
    bool notified=false;
    static uint32_t advance(uint32_t at,uint32_t size,uint32_t length) {
        return size<length-at?at+size:size-(length-at);
    }
    template<class Kick> void publish(uint32_t at,uint32_t hdr,uint32_t arg,uint32_t size,Kick kick) {
        link.tx[at/4]=hdr;
        link.tx[at/4+1]=arg;
        if(size==16) {
            const uint32_t name=advance(at,8,link.tn)/4;
            link.tx[name]=name0;
            link.tx[name+1]=name1; // includes NUL and zero padding
        }
        WpssVersion::fence();
        link.d[1]=advance(at,size,link.tn);
        WpssVersion::fence();
        kick();
    }
};
