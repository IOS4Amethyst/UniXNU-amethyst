#!/bin/bash
# Check the actual doconf output of the patched XNU tree, without compiling XNU.
set -eu
src=${1:?usage: check-second-static.sh PATCHED_XNU_TREE}
src=$(realpath "$src")
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
OBJROOT="$out" SRCROOT="$src" SOURCE="$src/config/MASTER" \
  csh "$src/SETUP/config/doconf" -cpu arm64 -soc bcm2837 -platform iPhoneOS \
  -m "$src/config" -d "$out" RELEASE
grep -Eq '^options[[:space:]]+CONFIG_SECOND_STATIC_TRUST_CACHE([[:space:]]|$)' "$out/config.RELEASE_ARM64"
grep -Eq '^options[[:space:]]+CONFIG_UNION_MOUNTS([[:space:]]|$)' "$out/config.RELEASE_ARM64"
echo 'PASS: RELEASE selects second static trust cache and union mounts'
