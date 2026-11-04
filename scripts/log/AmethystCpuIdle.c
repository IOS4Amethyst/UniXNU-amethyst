// SPDX-License-Identifier: BSD-2-Clause-Patent
// The CPU governor's idle-time helper: UniXNU-sheng 08cde20 patches/xnu/zzye-sheng-cpu-idle.patch (via
// rootstockos/UniXNU-asteroids zzye-board-cpu-idle.patch), body unchanged. amethyst's kernel is already
// built, so the helper is a separate object compiled with osfmk's recorded recipe (build-log.py) and linked
// in; cpu_to_processor and recount_processor_usage are present in this kernel.
#include <kern/processor.h>
#include <kern/recount.h>

/*
 * Board CPU governor helper: a CPU's idle time so far in mach absolute time units (recount brings it up to now
 * while the CPU idles), for board code that cannot see struct processor. 0 for a CPU number without a processor.
 */
uint64_t sheng_cpu_idle_mach(int cpu);
uint64_t
sheng_cpu_idle_mach(int cpu)
{
	processor_t p = cpu_to_processor(cpu);
	if (p == PROCESSOR_NULL) {
		return 0;
	}
	struct recount_usage usage = {};
	uint64_t idle = 0;
	recount_processor_usage(&p->pr_recount, &usage, &idle);
	return idle;
}

// CpuFreqProbe.cpp's touch boost watches native_hid_input_events[6] (ShengHidInput's report count). amethyst
// has no such driver yet; this weak zero keeps the boost idle until a touch driver defines the real counter.
__attribute__((weak)) volatile uint32_t native_hid_input_events[8];

// Cores XNU still refuses to signal (cpu_signal fails while SIGPdisabled is set, until the core takes its first
// IPI). V20/V21/V24 hung right after a stackshot logged "cpu_signal failed in DebuggerXCallEnter" for six cores:
// the secondaries never took an IPI because armcpu-self-ipi was off. Bit n set = core n still disabled.
#include <arm/cpu_data_internal.h>
#include <arm/cpu_internal.h>
uint32_t amethyst_sigp_disabled_mask(void);
uint32_t
amethyst_sigp_disabled_mask(void)
{
	uint32_t mask = 0;
	for (unsigned cpu = 0; cpu <= (unsigned)ml_get_max_cpu_number() && cpu < 32; cpu++) {
		cpu_data_t *d = (cpu_data_t *)CpuDataEntries[cpu].cpu_data_vaddr;
		if (d != NULL && (d->cpu_signal & SIGPdisabled)) {
			mask |= 1u << cpu;
		}
	}
	return mask;
}
