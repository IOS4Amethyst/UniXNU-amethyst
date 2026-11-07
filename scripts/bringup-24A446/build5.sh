#!/bin/bash
# build.sh N BIND DATA  -> staging/amethyst-ios27-iN (probe panic screen + log stub + configure exit probe + NVRAM gate nop)
set -e
N=$1; BIND=$2; DATA=$3
S=/tmp/claude-1000/-home-bluekyeet-SmallProject/6bef58f5-6d21-489e-b4c5-0ae037cad119/scratchpad
R=/home/bluekyeet/SmallProject; O=$R/.scratch/ios27/ipsw/24A446/kernelcache.decompressed
cd $R/.scratch/ios27-run5
GOT_OVERRIDE=$GOT_OVERRIDE python3 $S/id/patch-got.py $O k.macho $S/k${N}a.macho $BIND | grep -o "over 0x[0-9a-f]*.: [0-9]*" ; echo
python3 $S/id/patch-data.py $O $S/k${N}a.macho $S/k$N.macho $DATA | head -c 40; echo
rm $S/k${N}a.macho
PANIC_CAVE_AT=0xfffffff00ab11700 PANIC_CAVE_END=0xfffffff00ab13ff0 UNIXNU_UEFI=$R/uefi-asteroids python3 $S/coreprobe/scripts/patch-xnu-entry-probe-cave.py $S/k$N.macho --no-reset --panic-screen 2>&1 | tail -1 | cut -c1-60
python3 $S/id/logstub.py $S/k$N.macho
python3 $S/id/pmgrfix.py $S/k$N.macho
python3 $S/id/pmgrprobe.py $S/k$N.macho
if [ -n "$VBV" ]; then python3 $S/id/vbvprobe.py $S/k$N.macho; fi
if [ -n "$HANG" ]; then python3 $S/id/hang.py $S/k$N.macho $HANG; fi
cp $R/.scratch/ios27-run5/payload-probe-root27.img payload-i$N.img
mcopy -o -i payload-i$N.img $S/k$N.macho ::/EFI/UniXNU/Payloads/kernel.macho
if [ -n "$EXTRA_ARGS" ]; then mtype -i payload-i$N.img ::/EFI/UniXNU/unixnu.toml | sed "0,/^args = \"/s//args = \"$EXTRA_ARGS /" > $S/toml$N; grep -q "$EXTRA_ARGS" $S/toml$N; mcopy -o -i payload-i$N.img $S/toml$N ::/EFI/UniXNU/unixnu.toml; rm $S/toml$N; fi
mtype -i payload-i$N.img ::/EFI/UniXNU/Payloads/kernel.macho | cmp - $S/k$N.macho
cd $R && TMPDIR=$R/.scratch/tmp python3 UniXNU-amethyst-ios27/scripts/pack-ios27.py --container staging/amethyst-tap-v60/Mu-amethyst-tap-v60.img --payload .scratch/ios27-run5/payload-i$N.img --uefi-fd .scratch/uefi-darwinboot/amethyst-gmu-pool-db3c75734.fd --out staging/amethyst-ios27-i$N 2>&1 | tail -1
rm -f .scratch/ios27-run5/payload-i$N.img
