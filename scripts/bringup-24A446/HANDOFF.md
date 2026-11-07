# Handoff: iOS 27 24A446 bring-up — AMFI blocker resolved, new blocker is kext loading

Read `README.md` in this directory first for background. **The AMFI/CoreEntitlements entitlement-dict
blocker that earlier revisions of this file were about is fixed** (see below). `/sbin/launchd` now
runs and starts loading kernel extensions; the current blocker is a crash during that, described at
the bottom of this file.

## The AMFI blocker: what it was, and the fix

`AMFI: failed to create OSDictionary copy: 14` at exec was caused by one mis-set constant, not a
code-signing or entitlements-delivery problem. AppleMobileFileIntegrity's GOT slot for Apple's
`PAGE_SHIFT_CONST` global was still bound to Apple's own value. CoreEntitlements' DER iterator reads
that slot as a stack-depth sanity check: `sp & ((1 << shift) - 1) < 0x80`, assuming the stack is
aligned to `1 << shift`. That's true on Apple's 16 KiB-page kernel (`shift == 14`, with 32 KiB guard
pages below the stack). Our native kernel uses 4 KiB pages and starts its stack 8 KiB into a 32
KiB-aligned allocation, so checking against a 16 KiB mask gives false "out of stack" positives at
ordinary call depths — the exact, literal mechanism behind the `OSDictionary copy: 14` error.

**This bug, and its fix, were already found and documented upstream** — not derived from scratch here.
`UniXNU/XnuPrelink`'s `fixtures/fileset-24A446-symbols.json` has a `sites` entry describing it almost
word for word, including the fix: rebind the slot to the value `15` (the real allocation-alignment
shift) instead of Apple's `14`. That fixture is for a different device's kernelcache (iPad17,3, not
our iPhone15,4/d37ap one — different `kernelcache_sha256`), so its addresses don't transfer directly,
but the mechanism does. The equivalent code was located directly in our own kernelcache by searching
for the `sp & mask < 0x80` instruction pattern inside AMFI's text:

```
kc 0xfffffff008f7c838   mov  x9, sp
kc 0xfffffff008f7c83c   and  x8, x8, x9
kc 0xfffffff008f7c840   cmp  x8, #0x80
kc 0xfffffff008f7c844   b.lo <error path>
```

Its GOT slot is at kernelcache `0xfffffff007ef0db0` (traced back via the `adrp`/`ldr` pair at
`0xf7c820`/`0xf7c824`), bound in every one of our builds to Apple's own `PAGE_SHIFT_CONST` location.
`stackshift.py` rebinds it: writes the raw value `15` into an unused scratch word in the panic-cave
region, then repoints the GOT slot at that word (a plain non-auth rebase, same encoding used
elsewhere in this bring-up). No kernel source rebuild needed — this is a GOT-slot binary patch, same
category as everything else in `bind.json`/`got-override.json`.

**Confirmed fixed on hardware (i88):** booted with `STACKSHIFT=1` plus the `CEFAIL`/`CEIT` diagnostic
probes still active from `ceprobe.py`. Neither probe fired — the entitlement-dict build no longer
fails. The boot proceeded to a new, later, unrelated crash (below), which only happens once launchd
is genuinely running and processing further work. `STACKSHIFT=1` should be a permanent part of every
build from here; there is no reason to omit it.

**Lesson for next time:** before spending effort re-deriving a fix by reading disassembly or kernel
source from scratch, check `UniXNU/XnuPrelink`'s `fixtures/*-symbols.json` for the relevant kernelcache
build, and check whether another UniXNU device repo (e.g. `UniXNU/UniXNU-sheng`) has already booted
the same OS build successfully — their patches, prelink inputs and reports are often the fastest path
to a real fix, faster than re-deriving one independently.

## Current blocker: crash loading a kext, during/after launchd startup

With `STACKSHIFT=1`, i88 reached a new kernel data abort:

```
pc  0xfffffff00a8d5ddc   __ZN11OSMetaClass11postModLoadEPv +0x2e4
lr  (PAC-signed, not yet stripped in the dump)
```

Call chain (from the panic's frame list, symbolized):

```
OSKext::loadKextWithIdentifier(const char*, ...)   0xfffffff00a8e9ad4 +0x54
OSKext::loadKextWithIdentifier(OSString*, ...)     0xfffffff00a8e9bf4 +0xd8
OSKext::load(...)                                   0xfffffff00a8ead68 +0x18c
OSKext::start(bool)                                 0xfffffff00a8ee03c +0x364
OSRuntimeInitializeCPP(...)                         0xfffffff00a901d70 +0xe0
OSMetaClass::postModLoad(void*)                     0xfffffff00a8d5ddc +0x2e4   <- fault
```

This is a **dynamically loaded kext** (not one of the prelinked/retained kexts this whole bring-up has
been binding GOT slots for) being loaded fresh from disk and running its own C++ static-initializer
registration (`OSRuntimeInitializeCPP` → `OSMetaClass::postModLoad`). That's a different code path
from everything addressed so far: there's no GOT-slot stub to rebind here, this is the kernel's kext
loader processing a Mach-O it just mapped in, and something about that processing is faulting.

**Not yet investigated:**
- Which kext is being loaded (`OSKext::loadKextWithIdentifier` took a name/identifier argument —
  worth reading `x0`/register state at one of the outer frames, or adding a probe at
  `loadKextWithIdentifier`'s entry to log the identifier string, the same IOLog-cave technique used
  throughout this bring-up).
- Whether this is a kext our board's own patch set introduces (check `patches/xnu-12377/` and
  `board.toml`/`porting.toml` for anything that causes a non-prelinked kext load this early) or a
  stock iOS 27 kext that fails because of something still missing in our bring-up (e.g. a missing
  retained-kext dependency it links against).
- Whether `UniXNU/UniXNU-sheng` hit the same crash — worth checking their `reports/` directory and
  `UniXNU/XnuPrelink`'s fixture for a 24A446-area `postModLoad`/kext-loading fix the same way the
  stack-shift bug was found, before re-deriving one from scratch.

## Build state to resume from

- `bind.json` (456 entries), `data.json`, `got-override.json` (4 entries, includes the `0xa8a5f7c` →
  `_cs_system_require_lv` and `0xa8a62b0` → `_csblob_get_teamid` map-error corrections), `stackshift.py`
  (new, required) are all current in this directory.
- `build5.sh N BIND DATA` env vars: `STACKSHIFT=1` (new, required), `TRUST=<trust-cache bundle path>`
  (regenerate with `tcparse.py`/`adtaddtc.py` from a local 24A446 IPSW — not committed), `HANG=
  "0xfffffff00a1c1604 0xfffffff00a7a6b70"` (keeps the log on screen past two earlier panic sites;
  still harmless to include), `CEPROBE=1` optional now (the entitlement-dict failure it watches for
  no longer happens, but it's inert/harmless to leave on). No `CSBYPASS`, no `MXPROBE` (`_kprintf`
  caused an unrelated, reproducible early-boot regression — see README.md; don't retry it).
- RAM-boot only: `fastboot -s <serial> boot <image>`. Never flash, erase, `set_active`, or write
  Android userdata.
