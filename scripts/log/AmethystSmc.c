// SPDX-License-Identifier: BSD-2-Clause-Patent
// sheng_smc_call from rootstockos/UniXNU-asteroids package/BootWatchdogPolicy.c (unchanged): an SMCCC call with x0-x7 in and out.
// GpuPowerProbe.cpp mode 15 uses it for the PIL/PAS calls that authenticate the zap shader.
#include <stdint.h>
void sheng_smc_call(uint64_t r[8]);
void sheng_smc_call(uint64_t r[8]) {
 register uint64_t x0 __asm("x0")=r[0];
 register uint64_t x1 __asm("x1")=r[1];
 register uint64_t x2 __asm("x2")=r[2];
 register uint64_t x3 __asm("x3")=r[3];
 register uint64_t x4 __asm("x4")=r[4];
 register uint64_t x5 __asm("x5")=r[5];
 register uint64_t x6 __asm("x6")=r[6];
 register uint64_t x7 __asm("x7")=r[7];
 __asm__ volatile("smc #0" : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3),"+r"(x4),"+r"(x5),"+r"(x6),"+r"(x7) : :
                  "x8","x9","x10","x11","x12","x13","x14","x15","x16","x17","memory");
 r[0]=x0;r[1]=x1;r[2]=x2;r[3]=x3;r[4]=x4;r[5]=x5;r[6]=x6;r[7]=x7;
}
