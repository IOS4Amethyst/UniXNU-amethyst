#include "AmethystSpmiRead.h"
#include <assert.h>
static unsigned writes,waits,mode;
static uint32_t rd(void *p,unsigned o){(void)p;assert(o==3*32+8 || o==3*32+24);return o==3*32+24?0x51:mode;}
static void wr(void *p,unsigned o,uint32_t v){(void)p;assert(o==3*32 && v==((1u<<27)|(5u<<4)));++writes;}
static void wait(void *p){(void)p;++waits;}
int main(void){
 uint8_t v=0xaa;uint32_t s=0;
 mode=1;assert(!amethyst_spmi_read8(0,rd,wr,wait,3,5,&v,&s) && v==0x51);
 mode=5;v=0xaa;assert(amethyst_spmi_read8(0,rd,wr,wait,3,5,&v,&s)==-5 && v==0xaa);
 mode=0;assert(amethyst_spmi_read8(0,rd,wr,wait,3,5,&v,&s)==-110 && waits==1000 && v==0xaa);
 assert(amethyst_spmi_read8(0,rd,wr,wait,1024,5,&v,&s)==-22 && writes==3);
 return 0;
}
