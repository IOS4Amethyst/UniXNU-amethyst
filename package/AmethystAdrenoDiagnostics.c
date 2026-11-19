// SPDX-License-Identifier: BSD-2-Clause-Patent
// Main's optional exec/swap telemetry only; NEVER accelerator stand-ins.
#include <stdint.h>
__attribute__((weak)) volatile uint32_t asteroids_exec_total, asteroids_exec_ngpu,
    asteroids_exec_bluetoothd, asteroids_exec_fdr;
__attribute__((weak)) char asteroids_exec_last[8][24], asteroids_exec_ngpu_path[64];
__attribute__((weak)) volatile uint32_t asteroids_exec_swaps;
__attribute__((weak)) volatile int32_t asteroids_exec_ngpu_uid;
__attribute__((weak)) volatile uint32_t asteroids_swap_end_calls, asteroids_swap_end_size,
    asteroids_swap_end_id, asteroids_swap_end_status, asteroids_swap_wait_calls;
__attribute__((weak)) char boot_progress_ext[8][48];
