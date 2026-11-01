// SPDX-License-Identifier: BSD-2-Clause-Patent
// Scanout-ownership primitives only, derived from UniXNU-sheng package/BootProgress.c
// at 08cde20edc3254ed6f7df8d0f5384fb887786e8f.
//
// Sheng's boot-progress console renderer is deliberately NOT imported: no font,
// no text drawing or log ring lives here. The native console is released only
// at the validated, normal-thread scanout handoff.
// These entry points exist so the Legacy display driver can claim the firmware
// framebuffer without a competing console writer, and they report that ownership
// truthfully.
//
// CRITICAL ORDERING: bsd_init() calls boot_progress_stage("bsd_init enter") as its
// very first statement, before PE_create_console() has run. Every entry point here
// must therefore be safe with no console, no framebuffer mapping and no clock.
// The early stage hook never locks or touches framebuffer memory. The later
// prepare hook uses the console's own lock-backed release operation.
#include <stdint.h>
#include <pexpert/pexpert.h>
#include <kern/clock.h>
#include <console/video_console.h>
#include <machine/machine_routines.h>

// Scheduler-side render suppression of the (absent) diagnostic renderer. Stays
// zero for this kernel; the atomic yield handshake is preserved so that a busy
// writer, if one is ever added, must finish before the display is released.
static unsigned render_busy;
static unsigned yield_requested;

// Set only by prepare_scanout(); proves a validated scanout copy took the frame.
volatile unsigned boot_progress_display_owned;

// Callers are C++ translation units expecting C linkage, and the build recipe
// compiles this file as C++, so the hook names must be pinned.
extern "C" {
int boot_progress_pulse_anchor(uintptr_t base, uint64_t bytes, unsigned w,
    unsigned h, unsigned rb);
int boot_progress_prepare_scanout(void);
void boot_progress_cancel_scanout(void);
void boot_progress_stage(const char *text);
}

// Registering a diagnostic anchor would require Sheng's renderer, so this kernel
// declines the anchor instead of pretending to have drawn one.
int boot_progress_pulse_anchor(uintptr_t base, uint64_t bytes, unsigned w,
    unsigned h, unsigned rb)
{
	(void)base; (void)bytes; (void)w; (void)h; (void)rb;
	return 0;
}

// Called before a validated scanout copy, in thread context. Returns 0 without
// taking ownership if another writer is mid-frame; the caller must then skip the
// copy rather than painting over that writer. Only after this succeeds does the
// driver believe it owns the firmware framebuffer.
int boot_progress_prepare_scanout(void)
{
	if (ml_at_interrupt_context() || !ml_get_interrupts_enabled()) return 0;
	if (__atomic_load_n(&boot_progress_display_owned, __ATOMIC_ACQUIRE)) return 1;
	__atomic_store_n(&yield_requested, 1, __ATOMIC_RELEASE);
	if (__atomic_exchange_n(&render_busy, 1, __ATOMIC_ACQUIRE)) {
		__atomic_store_n(&yield_requested, 0, __ATOMIC_RELEASE);
		return 0;
	}
	// gc_enable(FALSE) disables drawing, then takes/releases VCPUTC_LOCK to
	// drain an in-flight native character before we return to the presenter.
	initialize_screen(NULL, kPEReleaseScreen);
	__atomic_store_n(&boot_progress_display_owned, 1, __ATOMIC_RELEASE);
	__atomic_store_n(&yield_requested, 0, __ATOMIC_RELEASE);
	__atomic_store_n(&render_busy, 0, __ATOMIC_RELEASE);
	return 1;
}

void boot_progress_cancel_scanout(void)
{
	__atomic_store_n(&yield_requested, 0, __ATOMIC_RELEASE);
}

// No diagnostic stage log exists in this kernel. This runs before the console is
// created, so it must not print, allocate, or read the clock. The parameter is
// deliberately ignored rather than stored anywhere a later renderer could
// mistake for real progress state.
void boot_progress_stage(const char *text)
{
	(void)text;
}
