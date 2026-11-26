// SPDX-License-Identifier: BSD-2-Clause
#include "../../package/touch/WpssIpcrtrOpen.h"
#include <cassert>

struct Peer {
    uint32_t d[4]{},tx[8]{},rx[8]{};
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

    Peer q;
    q.negotiated();
    q.d[2]=q.d[3]=24;
    q.frame(0x2a0002,0x00010007); // Priority bits are not part of length.
    assert(q.step()==0 && q.d[2]==24 && !q.channel.rcid && !q.kicks);
    q.frame(0x52435049,0x005254);
    assert(q.step()==0 && q.d[2]==8 && q.channel.ackSent && q.kicks==1);
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
    invalid.rx[1]=7;invalid.frame(0x52435049,0x015254); // Missing NUL.
    assert(invalid.step()==-3 && !invalid.d[2] && !invalid.kicks);
    invalid.d[3]=32;
    assert(invalid.step()==-1 && !invalid.d[2] && !invalid.kicks);
}
