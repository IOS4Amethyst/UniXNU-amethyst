// SPDX-License-Identifier: BSD-2-Clause-Patent
// Weak zero stand-ins for symbols GpuPowerProbe.cpp (asteroids) references only in modes 18-19: asteroids' XNU exec/swap
// counters (patches amethyst's kernel does not carry) and the staged NativeAdreno accelerator (not built for amethyst yet).
// The static link needs a definition; asteroids' LegacyDisplayStubs.c gives the same weak zeros. Modes 1-16 never use them.
#include <stdint.h>
#include <stddef.h>
__attribute__((weak)) volatile uint32_t asteroids_exec_total, asteroids_exec_ngpu, asteroids_exec_bluetoothd, asteroids_exec_fdr;
__attribute__((weak)) char asteroids_exec_last[8][24];
__attribute__((weak)) char asteroids_exec_ngpu_path[64];
__attribute__((weak)) volatile uint32_t asteroids_exec_swaps;
__attribute__((weak)) volatile int32_t asteroids_exec_ngpu_uid;
__attribute__((weak)) volatile uint32_t asteroids_swap_end_calls, asteroids_swap_end_size, asteroids_swap_end_id,
    asteroids_swap_end_status, asteroids_swap_wait_calls;
__attribute__((weak)) void native_adreno_accelerator_static_init(void) __asm__("__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp");
__attribute__((weak)) void native_adreno_accelerator_static_init(void) {}
__attribute__((weak)) void *native_adreno_accelerator_allocate(void) {return NULL;}
__attribute__((weak)) unsigned native_adreno_accelerator_instance_size(void) {return 0;}
// V47: KeyStoreProbe.cpp (asteroids) writes its AppleKeyStore counters into the diagnostic extension rows, which amethyst's
// BootLog prints when defined.
__attribute__((weak)) char boot_progress_ext[8][48];
