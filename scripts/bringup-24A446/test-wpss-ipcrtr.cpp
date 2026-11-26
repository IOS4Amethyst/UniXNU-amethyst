// SPDX-License-Identifier: BSD-2-Clause
#include "../../package/touch/WpssIpcrtrOpen.h"
#include <cassert>

template<unsigned RxWords=8> struct Peer {
    uint32_t d[4]{},tx[8]{},rx[RxWords]{};
    WpssVersion v{d,tx,rx,sizeof tx,sizeof rx};
    WpssIpcrtrOpen channel{v};
    unsigned kicks=0;
    int step() {return channel.step([&]{++kicks;});}
    void negotiated() {v.sent=v.replied=v.agreed=true;}
    void frame(uint32_t cmd, uint32_t arg=0) {
        const auto i=d[3]/4;
        rx[i]=cmd;rx[i+1]=arg;d[3]=(d[3]+8)%sizeof rx;
        assert(d[2]!=d[3]);
    }
    void open(uint32_t cid=42) {frame(cid<<16|2,7);frame(0x52435049,0xff005254);}
};

int main() {
    // b55 saw an 18-byte unregistered OPEN beginning with LOOPBAC. Model
    // a complete wrapped name of that size, then an exact IPCRTR OPEN.
    Peer<16> other;
    other.negotiated();
    other.d[2]=other.d[3]=56;
    other.frame(0x10002,18);
    other.frame(0x504f4f4c,0x5f4b4341); // LOOPBACK_
    assert(other.step()==0 && other.d[2]==56 && !other.channel.ignoredOpens && !other.kicks);
    other.frame(0x5254534d,0x44433231);
    other.frame(0x00000045,0);
    assert(other.step()==0 && other.d[2]==24 && other.channel.ignoredOpens==1 && !other.channel.rcid && !other.kicks);
    other.open();
    assert(other.step()==0 && other.channel.rcid==42 && other.channel.ackSent && other.kicks==1);
    assert(other.tx[0]==0x2a0004);
    // Wrapped OPEN is queued between VERSION and VERSION_ACK; no channel TX before version agreement.
    Peer p;
    p.d[0]=p.d[1]=16;
    p.d[2]=p.d[3]=16;
    p.frame(0x10000);
    assert(p.step()==0 && p.kicks==2 && p.v.sent && p.v.replied && !p.v.agreed);
    assert(p.tx[4]==0x10000 && p.tx[6]==0x10001);
    p.open();p.frame(0x10001);
    assert(p.step()==0 && p.channel.rcid==42 && p.d[2]==8 && p.kicks==2);
    assert(p.step()==0 && p.v.agreed && p.d[2]==16 && p.kicks==2);

    // Only reserved space remains. READ_NOTIF is emitted once; never claim ACK or OPEN until committed.
    assert(p.step()==0 && p.kicks==3 && p.tx[0]==13 && p.d[1]==8 && !p.channel.ackSent);
    assert(p.step()==0 && p.kicks==3 && p.d[1]==8);
    p.d[0]=8;
    assert(p.step()==0 && p.kicks==4 && p.tx[2]==0x2a0004 && p.tx[3]==0 && p.channel.ackSent);
    p.open(); // Duplicate peer OPEN neither rebinds nor reissues the ACK.
    assert(p.step()==0 && p.kicks==5 && p.tx[4]==13 && p.d[2]==0 && !p.channel.openSent);
    assert(p.step()==0 && p.kicks==5);
    p.d[0]=24;
    p.frame(13);
    assert(p.step()==0 && p.kicks==7 && p.d[2]==8 && p.channel.openSent);
    assert(p.d[1]==8 && p.tx[6]==0x10002 && p.tx[7]==7 && p.tx[0]==0x52435049 && p.tx[1]==0x005254);
    p.frame(0x10004);p.frame(0x2a0005,1); // Following non-channel frame remains unread.
    assert(p.step()==1 && p.d[2]==16 && p.channel.openAcked && p.kicks==7);
    assert(p.step()==1 && p.d[2]==16 && p.kicks==7);
    uint32_t count=0,first=0,largest=0;
    assert(p.channel.peekIntents(count,first,largest)==0 && !count && p.d[2]==16);
    p.frame(0x800,9);
    assert(p.channel.peekIntents(count,first,largest)==1 && count==1 && first==9 && largest==0x800);
    assert(p.d[2]==16 && p.kicks==7); // Read-only, including a batch that wraps.
    p.rx[5]=0xffffffff;
    assert(p.channel.peekIntents(count,first,largest)==-3 && !count && p.d[2]==16);
    p.rx[4]=0x2b0005;
    assert(p.channel.peekIntents(count,first,largest)==-2 && !count && p.d[2]==16);

    Peer q;
    q.negotiated();
    q.d[2]=q.d[3]=24;
    q.frame(0x2a0002,0x00010007); // Priority bits are not part of length.
    assert(q.step()==0 && q.d[2]==24 && !q.channel.rcid && !q.kicks);
    assert(q.channel.openHeader==0x2a0002 && q.channel.openArg==0x10007 && !q.channel.openNameBytes);
    q.frame(0x52435049,0x005254);
    assert(q.step()==0 && q.d[2]==8 && q.channel.ackSent && q.kicks==1);
    assert(q.channel.openNameBytes==7 && q.channel.openName[0]=='I' && q.channel.openName[5]=='R' && q.channel.openName[6]==0);
    assert(q.step()==0 && !q.channel.openSent && q.kicks==2 && q.tx[2]==13);
    q.d[0]=q.d[1];
    assert(q.step()==0 && q.channel.openSent && q.kicks==3);
    q.frame(0x2a0004); // Wrong CID, leave it queued and do not signal success.
    assert(q.step()==-3 && q.d[2]==8 && !q.channel.openAcked && q.kicks==3);

    Peer early;
    early.open(); // Queued before the host begins negotiation.
    assert(early.step()==0 && early.channel.rcid==42 && early.d[2]==16 && !early.kicks);
    assert(early.step()==0 && early.v.sent && early.kicks==1);
    early.d[0]=early.d[1];
    early.frame(0x10001);early.frame(0x10000);
    assert(early.step()==0 && early.v.agreed && !early.v.replied && early.kicks==1);
    assert(early.step()==0 && early.v.replied && early.kicks==2);
    assert(early.step()==0 && early.channel.ackSent && early.kicks==3);

    Peer invalid;
    invalid.negotiated();
    invalid.frame(0x10004); // Correct CID but no local OPEN yet.
    assert(invalid.step()==-3 && !invalid.d[2] && !invalid.kicks);
    invalid.rx[0]=0x2a0005;
    assert(invalid.step()==-2 && !invalid.d[2] && !invalid.kicks);
    invalid.rx[0]=0x2a0002;invalid.rx[1]=0xffff;
    assert(invalid.step()==-3 && !invalid.d[2] && !invalid.kicks);
    assert(invalid.channel.openArg==0xffff && !invalid.channel.openNameBytes);
    invalid.rx[1]=7;invalid.frame(0x52435049,0x015254); // Missing NUL.
    assert(invalid.step()==-3 && !invalid.d[2] && !invalid.kicks);
    assert(invalid.channel.openNameBytes==7 && invalid.channel.openName[6]==1);
    invalid.d[3]=32;
    assert(invalid.step()==-1 && !invalid.d[2] && !invalid.kicks);
}
