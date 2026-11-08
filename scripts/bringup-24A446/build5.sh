#!/bin/bash
# build.sh N BIND DATA  -> staging/amethyst-ios27-iN (probe panic screen + log stub + configure exit probe + NVRAM gate nop)
set -e
N=$1; BIND=$2; DATA=$3
S=/tmp/claude-1000/-home-bluekyeet-SmallProject/6bef58f5-6d21-489e-b4c5-0ae037cad119/scratchpad
R=/home/bluekyeet/SmallProject; O=$R/.scratch/ios27/ipsw/24A446/kernelcache.decompressed
cd $R/.scratch/ios27-run5
: "${GOT_OVERRIDE:?GOT_OVERRIDE must be set to the absolute path of got-override.json}"
[ -f "$GOT_OVERRIDE" ] || { echo "GOT_OVERRIDE file not found: $GOT_OVERRIDE" >&2; exit 2; }
[ -f "$BIND" ] || { echo "BIND file not found: $BIND" >&2; exit 2; }
[ -f "$DATA" ] || { echo "DATA file not found: $DATA" >&2; exit 2; }
for f in patch-got.py patch-data.py logstub.py pmgrfix.py pmgrprobe.py; do
  [ -f "$S/id/$f" ] || { echo "missing scratchpad script: $S/id/$f" >&2; exit 2; }
done
# Preflight the optional probes we were asked for: they must exist AND not share a cave.
# (Two probes silently targeting overlapping caves cost a whole boot once.)
optional=""
[ -n "$VBV" ] && optional="$optional vbvprobe.py"
[ -n "$MXPROBE" ] && optional="$optional mxprobe.py"
[ -n "$CEPROBE" ] && optional="$optional ceprobe.py"
[ -n "$KEXTPROBE" ] && optional="$optional lkcentrystash.py kextnameprobe.py badcastfreeze.py"
[ -n "$PACPROBE" ] && optional="$optional pacprobe.py"
[ -n "$EXCPROBE" ] && optional="$optional excprobe.py"
[ -n "$ARRAYPROBE" ] && optional="$optional arrayprobe.py"
for f in $optional; do
  [ -f "$S/id/$f" ] || { echo "probe requested but missing: $S/id/$f" >&2; exit 2; }
done
# The probe lint runs as a preflight so cave overlaps and probe-code defects are caught here
# instead of on the device. Its exit status MUST be honoured: the previous form ended in
# `|| true`, which meant a failing lint could never stop a build.
if [ -n "$optional" ]; then
  python3 $R/UniXNU-amethyst-ios27/scripts/bringup-24A446/probe-lint.py $S/id $optional || {
    echo "probe-lint FAILED -- fix the probe before building" >&2; exit 1; }
fi
# NOTE: this used to be `... | grep -o "over 0x..."`, which masked a non-zero exit from
# patch-got.py (grep found nothing, the pipeline still succeeded) and let the build report
# success while producing no image. Let the script's own status decide, then filter the log.
gotlog=$S/got$N.log
if ! GOT_OVERRIDE=$GOT_OVERRIDE python3 $S/id/patch-got.py $O k.macho $S/k${N}a.macho $BIND >"$gotlog" 2>&1; then
  echo "patch-got.py FAILED:" >&2; cat "$gotlog" >&2; exit 1
fi
grep -o "rebound [0-9]* slots" "$gotlog" || { echo "patch-got.py produced no 'rebound' line:" >&2; cat "$gotlog" >&2; exit 1; }
python3 $S/id/patch-data.py $O $S/k${N}a.macho $S/k$N.macho $DATA | head -c 40; echo
rm $S/k${N}a.macho
PANIC_CAVE_AT=0xfffffff00ab11700 PANIC_CAVE_END=0xfffffff00ab13ff0 UNIXNU_UEFI=$R/uefi-asteroids python3 $S/coreprobe/scripts/patch-xnu-entry-probe-cave.py $S/k$N.macho --no-reset --panic-screen 2>&1 | tail -1 | cut -c1-60
python3 $S/id/logstub.py $S/k$N.macho
python3 $S/id/pmgrfix.py $S/k$N.macho
python3 $S/id/pmgrprobe.py $S/k$N.macho
if [ -n "$VBV" ]; then python3 $S/id/vbvprobe.py $S/k$N.macho; fi
if [ -n "$HANG" ]; then python3 $S/id/hang.py $S/k$N.macho $HANG; fi
if [ -n "$TRUST" ]; then python3 $S/id/tcfix.py $S/k$N.macho; fi
if [ -n "$STACKSHIFT" ]; then python3 $S/id/stackshift.py $S/k$N.macho; fi
if [ -n "$PAGESHIFT" ]; then python3 $S/id/pageshift.py $S/k$N.macho; fi
if [ -n "$CSBYPASS" ]; then python3 $S/id/csbypass.py $S/k$N.macho; fi
if [ -n "$MXPROBE" ]; then python3 $S/id/mxprobe.py $S/k$N.macho; fi
if [ -n "$CEPROBE" ]; then python3 $S/id/ceprobe.py $S/k$N.macho; fi
if [ -n "$KEXTPROBE" ]; then python3 $S/id/lkcentrystash.py $S/k$N.macho; fi
if [ -n "$KEXTPROBE" ]; then python3 $S/id/kextnameprobe.py $S/k$N.macho; fi
if [ -n "$KEXTPROBE" ]; then python3 $S/id/badcastfreeze.py $S/k$N.macho; fi
if [ -n "$PACPROBE" ]; then python3 $S/id/pacprobe.py $S/k$N.macho; fi
if [ -n "$EXCPROBE" ]; then python3 $S/id/excprobe.py $S/k$N.macho; fi
if [ -n "$ARRAYPROBE" ]; then python3 $S/id/arrayprobe.py $S/k$N.macho; fi
cp $R/.scratch/ios27-run5/payload-probe-root27.img payload-i$N.img
mcopy -o -i payload-i$N.img $S/k$N.macho ::/EFI/UniXNU/Payloads/kernel.macho
if [ -n "$TRUST" ]; then mtype -i payload-i$N.img ::/EFI/UniXNU/Payloads/kernel.macho.adt > $S/adt$N; python3 $S/id/adtaddtc.py $S/adt$N $S/adt$N.tc; mcopy -o -i payload-i$N.img $S/adt$N.tc ::/EFI/UniXNU/Payloads/kernel.macho.adt; mcopy -o -i payload-i$N.img $TRUST ::/EFI/UniXNU/Payloads/kernel.macho.trustcache; rm $S/adt$N $S/adt$N.tc; mdir -i payload-i$N.img ::/EFI/UniXNU/Payloads; fi
if [ -n "$EXTRA_ARGS" ]; then mtype -i payload-i$N.img ::/EFI/UniXNU/unixnu.toml | sed "0,/^args = \"/s//args = \"$EXTRA_ARGS /" > $S/toml$N; grep -q "$EXTRA_ARGS" $S/toml$N; mcopy -o -i payload-i$N.img $S/toml$N ::/EFI/UniXNU/unixnu.toml; rm $S/toml$N; fi
mtype -i payload-i$N.img ::/EFI/UniXNU/Payloads/kernel.macho | cmp - $S/k$N.macho
cd $R && TMPDIR=$R/.scratch/tmp python3 UniXNU-amethyst-ios27/scripts/pack-ios27.py --container staging/amethyst-tap-v60/Mu-amethyst-tap-v60.img --payload .scratch/ios27-run5/payload-i$N.img --uefi-fd .scratch/uefi-darwinboot/amethyst-gmu-pool-db3c75734.fd --out staging/amethyst-ios27-i$N 2>&1 | tail -1
rm -f .scratch/ios27-run5/payload-i$N.img
