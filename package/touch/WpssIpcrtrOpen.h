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
    unsigned ignoredOpens=0;
    uint8_t openName[7]={}, openNameBytes=0;
    bool ackSent=false, openSent=false, openAcked=false;

    // ponytail: one local intent and one unfragmented receive; stop before QRTR dispatch.
    static constexpr uint32_t receiveBytes=128, receiveId=1;
    bool advertised=false, received=false;
    uint32_t receivedBytes=0, nextCommand=0xffff;
    // Mode 7: answer one RX_INTENT_REQ like Sheng/Linux (INTENT, then RX_INTENT_REQ_ACK).
    static constexpr uint32_t grantBytes=1024, grantId=2;
    bool grantRequests=false, granted=false;
    uint32_t requestedBytes=0, requests=0, receivedId=0, notifies=0;
    uint8_t payload[grantBytes]={};

    template<class Kick> int receiveOne(Kick kick) {
        if(received)return 1;
        if(!openAcked)return -1;
        auto d=link.d;
        const uint32_t tt=d[0],th=d[1],rt=d[2],rh=d[3],tn=link.tn,rn=link.rn;
        if(tn<40 || rn<24 || ((tn|rn|tt|th|rt|rh)&7) || tt>=tn || th>=tn || rt>=rn || rh>=rn)return -1;
        WpssVersion::fence();
        const uint32_t free=tt>th?tt-th:tn-th+tt;
        if(!advertised) {
            if(free<32)return 0;
            const uint32_t at=advance(th,8,tn)/4;
            link.tx[at]=receiveBytes;link.tx[at+1]=receiveId;
            link.tx[th/4]=5u|(localCid<<16);link.tx[th/4+1]=1;
            WpssVersion::fence();d[1]=advance(th,16,tn);WpssVersion::fence();
            advertised=true;kick();return 0;
        }
        if(rt==rh)return 0;
        const uint32_t hdr=link.rx[rt/4];nextCommand=hdr&0xffff;
        const uint32_t available=rh>rt?rh-rt:rn-rt+rh;
        if(nextCommand==5 && (hdr>>16)==rcid) {
            uint32_t count,first,largest;
            const int rc=peekIntents(count,first,largest);
            if(rc!=1)return rc;
            WpssVersion::fence();d[2]=advance(rt,8+8*count,rn);WpssVersion::fence();
            return 0; // Not transmitting data: no remote intent table needed yet.
        }
        if(grantRequests && nextCommand==13 && hdr==13) { // READ_NOTIF
            if(link.rx[rt/4+1])return -3;
            WpssVersion::fence();d[2]=advance(rt,8,rn);WpssVersion::fence();
            ++notifies;kick();return 0;
        }
        if(grantRequests && nextCommand==7 && (hdr>>16)==rcid) { // RX_INTENT_REQ, arg = bytes
            const uint32_t want=link.rx[rt/4+1];
            const bool grant=!granted && want && want<=grantBytes;
            if(free<(grant?24u:8u)+16)return 0; // Keep the full gap and READ_NOTIF reserve.
            requestedBytes=want;++requests;
            uint32_t at=th;
            if(grant) {
                const uint32_t pair=advance(at,8,tn)/4;
                link.tx[pair]=grantBytes;link.tx[pair+1]=grantId;
                link.tx[at/4]=5u|(localCid<<16);link.tx[at/4+1]=1;
                at=advance(at,16,tn);
            }
            link.tx[at/4]=8u|(localCid<<16);link.tx[at/4+1]=grant;
            WpssVersion::fence();
            d[2]=advance(rt,8,rn);d[1]=advance(at,8,tn);
            WpssVersion::fence();
            granted=granted||grant;kick();return 0;
        }
        if(nextCommand!=9 || (hdr>>16)!=rcid)return -2;
        const uint32_t liid=link.rx[rt/4+1];
        const uint32_t limit=liid==receiveId?receiveBytes:(granted && liid==grantId?grantBytes:0);
        if(!limit)return -3;
        if(available<16)return 0;
        const uint32_t at=advance(rt,8,rn)/4;
        const uint32_t chunk=link.rx[at],left=link.rx[at+1];
        if(!chunk || chunk>limit || left)return -3;
        const uint32_t size=(16+chunk+7)&~7u;
        if(size>=rn)return -3;
        if(available<size || free<24)return 0;
        for(uint32_t i=0;i<chunk;++i) {
            const uint32_t pos=advance(rt,16+i,rn);
            payload[i]=uint8_t(link.rx[pos/4]>>((pos&3)*8));
        }
        WpssVersion::fence();d[2]=advance(rt,size,rn);
        receivedBytes=chunk;received=true;receivedId=liid;
        publish(th,6u|(localCid<<16),liid,8,kick); // No INTENT_REUSE negotiated.
        return 1;
    }

    // Read-only checkpoint: observe the first queued remote intent batch.
    // Do not advertise receive storage or consume a frame without a live endpoint.
    int peekIntents(uint32_t &count,uint32_t &firstId,uint32_t &largest) const {
        count=firstId=largest=0;
        if(!openAcked)return -1;
        const uint32_t rt=link.d[2],rh=link.d[3],rn=link.rn;
        if(rn<24 || ((rn|rt|rh)&7) || rt>=rn || rh>=rn)return -1;
        if(rt==rh)return 0;
        WpssVersion::fence();
        const uint32_t hdr=link.rx[rt/4],n=link.rx[rt/4+1];
        if((hdr&0xffff)!=5 || (hdr>>16)!=rcid)return -2;
        if(!n || n>(rn-16)/8)return -3;
        const uint32_t available=rh>rt?rh-rt:rn-rt+rh;
        if(available<8+8*n)return 0;
        uint32_t id=0,maxSize=0;
        for(uint32_t i=0;i<n;++i) {
            const uint32_t at=advance(rt,8+8*i,rn)/4;
            const uint32_t size=link.rx[at];
            if(!size)return -3;
            if(!i)id=link.rx[at+1];
            if(size>maxSize)maxSize=size;
        }
        count=n;firstId=id;largest=maxSize;
        return 1;
    }

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
                const uint32_t length=arg&0xffff;
                auto nameByte=[&](uint32_t i) {
                    const uint32_t at=advance(rt,8+i,rn);
                    return uint8_t(link.rx[at/4]>>((at&3)*8));
                };
                openHeader=hdr;openArg=arg;openNameBytes=0;
                for(auto &byte:openName)byte=0;
                for(uint32_t i=0;i<sizeof openName && i<length && i+8<available;++i)
                    openName[openNameBytes++]=nameByte(i);
                if(!cid || length<2 || length>32)return -3;
                size=(8+length+7)&~7u;
                if(size>=rn)return -3; // Preserve the FIFO's full/empty gap.
                if(available<size)return 0;
                for(uint32_t i=0;i<length;++i)
                    if((nameByte(i)==0)!=(i==length-1))return -3;
                const bool matches=length==7 && link.rx[advance(rt,8,rn)/4]==name0 &&
                    (link.rx[advance(rt,8,rn)/4+1]&0xffffff)==name1;
                if(!matches) {
                    if(cid==rcid)return -3; // Don't accept a rename of the tracked channel.
                    ++ignoredOpens; // Like Sheng: consume a complete unregistered OPEN, without ACK.
                    break;
                }
                if(rcid && rcid!=cid)return -3;
                rcid=cid; // Stash even before VERSION_ACK, so it cannot block version frames behind it.
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
