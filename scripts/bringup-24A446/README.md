# iOS 27 24A446 hardware bring-up (test only)

Post-link patches applied to the prelinked kernel for amethyst test boots (`fastboot boot`, RAM only).
They bind retained-kext GOT/data slots that XnuPrelink left on `_unixnu_fileset_unmapped` to reviewed
native symbols. Every entry was checked against the native body (exact sequence, thunk shape, or
position among already-mapped neighbours). Inputs are a local 24A446 kernelcache and the
`ios27-run5` prelinked kernel; neither is in git.

- `bind.json`: kernelcache function address -> native symbol, applied to slots still on the stub (`patch-got.py`).
- `data.json`: kernelcache data address -> native symbol (`patch-data.py`). Includes
  `OSString::metaClass` (0xfffffff007cc9668), which the prelinker data map is missing.
- `got-override.json`: map errors, rebound even where the prelinker already bound the slot
  (`GOT_OVERRIDE=` env for `patch-got.py`): `thread_wakeup_prim` was mapped to an `OUTLINED` helper,
  and 0xaabceac is `IOSimpleLockFree`.
- `logstub.py`: `_unixnu_fileset_unmapped` logs `UNMAPPED +<caller offset> x0 x1` and returns 0.
  `site.py` turns the caller offset into the stub, GOT slot and original target.
- `pmgrfix.py` / `pmgrprobe.py`: ApplePMGR binding slide fix and publication probe.
- `hang.py` / `vbvprobe.py`: freeze APFS error cleanup so the log stays on screen; superblock probe.
- `build5.sh N BIND DATA`: patch, add the panic screen, pack `staging/amethyst-ios27-iN`.

Reached with i67: the root volume (Rave24A446.D370S, E89DE0E4-...) mounts and devfs mounts.
`/sbin/launchd` exec then fails with ENOEXEC (errno 8).

These bindings belong upstream in XnuPrelink's reviewed-name fixture; this directory records them until then.
