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

## Current blocker: OSKext::recordIdentifierRequest crashes on a corrupted global, loading com.apple.kpi.bsd

With `STACKSHIFT=1`, boot reaches very early kext bootstrap (before launchd, in fact —
`KLDBootstrap::readStartupExtensions` is one of the first things that runs) and crashes in
`OSMetaClassBase::safeMetaCast`, dereferencing an object pointer that's garbage
(`0xdc008005` -- not null, not a valid kernel address, which are always `0xfffffff0xxxxxxxx`).

Call chain (addresses are `k.macho`, the kernel we build and boot — see the two-binary warning below
the chain):
```
KLDBootstrap::readStartupExtensions            0xfffffff00ab0d2c0-ish (from IOCatalogue block invoke)
OSKext::loadKextWithIdentifier(OSString*,...)  0xfffffff00a8e9b1c
OSKext::recordIdentifierRequest(OSString*)     0xfffffff00a8e9fec  +0x30  <- virtual call through [x0,#272]
OSMetaClassBase::safeMetaCast                  0xfffffff00a8d4e10  +0x1c  <- fault (x0 == 0xdc008005)
```

The faulting operation is a **virtual call, not a `safeMetaCast` call site**: `recordIdentifierRequest`
does `ldr x0,[x22,#1312]` (the global), null-checks it, then `ldr x16,[x0]` / `autda` / `ldr x8,[x16,#272]`
/ `blraa`. With `x0 == 0xdc008005` the `ldr x16,[x0]` itself faults. The global's own null guard
(`cbz x8, <finish>` at `0xa8ea010`) is present and correct, so by the time we get there the slot holds a
**non-null garbage** value, not null.

**The identifier being loaded, read directly off the device: `com.apple.kpi.bsd`** (a `kextnameprobe.py`
+ `badcastfreeze.py` pair stashed it at the real `loadKextWithIdentifier` entry and rendered it as
plain hex-digit text at the crash site — see "How this was found" below; getting a readable string
out of a frozen panic screen took three iterations because `IOLog`'s `%llx`/`%s` handling is
unreliable here, also documented below).

**Root cause, from reading `libkern/c++/OSKext.cpp` in the reference XNU tree (caveat: this is the
17.7/`xnu-10063` tree, not 24A446's `xnu-12377` — re-check against the real one if it's available,
same caveat as everywhere else in this bring-up):** `recordIdentifierRequest` reads a static global,
`sAllKextLoadIdentifiers` (an `OSSharedPtr<OSSet>`), set exactly once, in `OSKext::initialize()`
(`iokit/Kernel/IOStartIOKit.cpp`, `StartIOKit` → `OSKext::initialize()`), via
`sAllKextLoadIdentifiers = OSSet::withCapacity(kOSKextTypicalLoadCount);`. If that assignment had
simply never run, the pointer would still read as null, `recordIdentifierRequest`'s own
`if (!sAllKextLoadIdentifiers || ...) goto finish;` guard would catch it safely, and there would be
no crash. A **non-null but invalid** value (`0xdc008005`) means something did write to that global's
storage — just not a valid `OSSet*` — which points at either: `OSKext::initialize()` running with
something already broken underneath it (e.g. the allocator it calls through), or a different global
aliasing/overwriting this one's storage (the kind of prelink/binding mistake this bring-up has found
several times already, though this is plain native `com.apple.kernel` code, not a retained-kext GOT
slot, so it wouldn't be the *same* class of bug as the earlier ones).

**Read the addresses in this file section with care.** Everything above this line was traced against
the wrong binary. There are two completely different kernels in this bring-up and they do not share a
layout:

| | Apple's `kernelcache.decompressed` (24A446, from the IPSW) | **`k.macho`** (built in `.scratch/ios27-run5`, and the thing `build5.sh` actually patches) |
| --- | --- | --- |
| `OSKext::recordIdentifierRequest` | `0xfffffff00aa787ec` | **`0xfffffff00a8e9fec`** |
| `OSKext::initialize` | `0xfffffff00aa7440c` | **`0xfffffff00a8e0480`** |
| `OSMetaClassBase::safeMetaCast` | `0xfffffff00aa4ebc0` | **`0xfffffff00a8d4e10`** |
| `sAllKextLoadIdentifiers` | `0xfffffff00af99ed0` | **`0xfffffff00af67520`** |
| `__TEXT_EXEC` size | `0x2a44000` | `0x2930000` |

The probes patch `k.macho` (`patch-got.py`/`patch-data.py` produce it from the kc, but every probe
edits `k.macho` afterwards), so **all probe `SITE`/`GLOBAL`/`CAVE` addresses must be `k.macho`
addresses**. The per-probe `assert W(SITE)==<expected instruction>` guards are what catch this: all
six probes' asserts match `k.macho` and all six fail against the kc. If an assert ever fires, check
which binary you decoded from before changing the constant — the constant is probably right and the
disassembly you did it from was of the other kernel.

Cross-check that settles it, without needing to trust either address: in `k.macho`,
`OSKext::recordIdentifierRequest` (`0xa8e9fec`) is `adrp x22, <page>` + `ldr x8, [x22, #1312]` →
`0xfffffff00af67520`, and inside `OSKext::initialize` (`0xa8e0480`) the instruction at
`0xfffffff00a8e0664` is `str x19, [x22, #1312]`, storing the `OSSet` that `OSSet::withCapacity(150)`
just returned (`mov x19, x0` at `0xa8e0638`). Same slot, written once, read by the crashing function —
that is `sAllKextLoadIdentifiers`, in the binary we build.

**Both binaries are internally consistent; neither address is "wrong".** A register-tracking scan
(`adrp` target propagated until that register is clobbered, then an `ldr`/`str` with the matching
offset) gives, for `k.macho` on `0xaf67520`, exactly six references, all credible:

```
0xfffffff00a8e0640  ldr x0  OSKext::initialize +0x1c0        (the null check before re-creating)
0xfffffff00a8e0664  str x19 OSKext::initialize +0x1e4        (the single assignment)
0xfffffff00a8ea00c  ldr x8  OSKext::recordIdentifierRequest +0x20   (the guard -- the crash's source)
0xfffffff00a8ea030  ldr x0  OSKext::recordIdentifierRequest +0x44
0xfffffff00a8ea060  ldr x0  OSKext::recordIdentifierRequest +0x74
0xfffffff00a8f2c94  ldr x27 OSKext::handleRequest +0x1250
```

and for the kc on `0xaf99ed0`, exactly four, the same functions at the kc's own addresses
(`OSKext::initialize +0x4e4`, `recordIdentifierRequest +0x24`, etc.); each address has zero
references in the other binary. So 0xaf67520 is `k.macho`'s slot and 0xaf99ed0 is the kc's slot, and
a probe must use the one for the binary it edits. The bare-address scans done earlier in this
bring-up (looking for a raw `<Q` pointer, or for any `adrp`+`offset` pair) were unreliable in both
directions: they cannot see `adrp`-plus-register-offset addressing, and without register tracking they
report unrelated instructions that happen to share a page.

**`OSKext::initialize()` does get called in our build, so the global is not simply never set.** In
`k.macho` the only caller is `_iokit_post_constructor_init +0x28` (`0xfffffff00a93b450`), which calls
`OSKext::initialize` (`0xa8e0480`) unconditionally as part of IOKit's constructor init. That rules out
the "the assignment never ran, so the slot is uninitialised" theory outright — and it means the
`0xdc008005` in the crash is a *later* corrupting write (or a bogus pointer being stored), which is
the more interesting branch of the fork this section has been narrowing.

**Not yet investigated:**
- Whether `OSKext::initialize()` (called from `StartIOKit`) actually runs to completion before
  `loadKernelComponentKexts` does, in our build specifically — add a probe right after
  `OSKext::initialize()` returns that reads `sAllKextLoadIdentifiers`'s value directly and logs it
  (same hex-render-to-text technique as `badcastfreeze.py`). If it's already garbage there, the bug
  is in `initialize()` or something it calls; if it's still correctly null/fine there and only
  becomes `0xdc008005` later, something *else* overwrites it in between, which is a very different
  (and probably more interesting) bug.
- Whether `UniXNU/UniXNU-sheng` (which did boot this exact OS build to SpringBoard) hit the same
  thing — their build succeeded, so whatever's different about their `OSKext::initialize()` path is
  worth a direct comparison. Nothing specific was found in their patches on a first pass (`git grep`
  for `OSKext::initialize`/`sAllKextLoadIdentifiers`/`sKernelComponentNames` across `upstream-unixnu`,
  `UniXNU-sheng`, and `XnuPrelink` came up empty), but that doesn't rule out a difference in ordinary
  (unpatched) native kernel behavior between their build and ours.

### How this was found: getting a readable string out of a frozen panic screen

Three real dead ends on the way here, each worth knowing about before trying again:
1. **`%llx`/`%s` through `IOLog` is unreliable for this kind of value.** Pointer-shaped arguments
   printed as the literal string `<ptr>` (confirmed on-device, not a blurry photo). Masking to 32 bits
   first didn't help (rules out a magnitude heuristic). A 4-argument attempt came back completely
   scrambled — one slot showed a *different* argument's real value, i.e. redaction here can break
   argument correspondence itself, not just blank one value.
2. **Calling `_kprintf` instead of `_IOLog`** did stop the redaction for what little could be read,
   but caused a reproducible crash much earlier in boot (confirmed on two identical boots). Root
   cause not found; not worth re-attempting without a specific new reason to.
3. **Writing data into the `__TEXT_EXEC` panic-cave region at runtime faults.** That region is r-x
   only even though it's where every *build-time*-baked probe (format strings, patched instructions)
   lives safely — the difference is build-time bytes vs. a runtime `str`. Needed an actual writable
   scratch location: the last 64–128 bytes of `__DATA` (zero-fill padding at the segment's page-aligned
   tail) works.
4. **Pre-filling that writable scratch buffer with literal text at *build* time hung the boot before
   even the first kernel log line** (`iBoot version:` / `iBoot Stage 2 version:`, far earlier than
   any previous failure in this bring-up). Something very early in boot doesn't tolerate pre-set
   content in that `__DATA` tail. Fix: leave the buffer entirely zero at build time (same as every
   other scratch location used successfully elsewhere) and have the probe's own runtime code copy the
   static text in too, from a small literal embedded in its own (read-only) code cave.

What worked, and is the pattern worth reusing for the next probe: `kextnameprobe.py` patches
`loadKextWithIdentifier`'s real entry, calls `OSString::getCStringNoCopy()` on the incoming
identifier, and silently copies 20 raw bytes into a writable `__DATA` scratch buffer on *every* kext
load attempt (cheap, no printing, too fast to ever photograph anyway). `badcastfreeze.py` patches the
exact instruction that's known to fault, *conditionally* — only when the pointer being dereferenced
has zero upper 32 bits (real kernel pointers never do), so it doesn't trip on the thousands of
legitimate uses of the same function elsewhere in the kernel — and on that path, hex-renders the
stashed identifier as plain ASCII text entirely at runtime (no `%`-conversions reach `IOLog` at all)
before hanging so the screen stays readable.

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
