#!/bin/sh
# Build a compiler from a previously verified SDK and compare three native emissions.
set -eu

if [ "$#" -ne 2 ]; then
    printf 'Usage: %s VERIFIED_NATIVE_SDK NEW_OUTPUT_DIRECTORY\n' "$0" >&2
    exit 2
fi

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
sdk=$(CDPATH='' cd -- "$1" && pwd -P)
source_file="$repo_root/selfhost/compiler.wysna"
compiler="$sdk/libexec/wysna-compiler"
zig="$sdk/toolchain/zig/zig"

case "$(uname -s):$(uname -m)" in
    Darwin:arm64) target=aarch64-macos.13.0-none ;;
    Linux:x86_64) target=x86_64-linux-musl ;;
    *)
        printf 'This bootstrap supports macOS arm64 and Linux x64.\n' >&2
        exit 2 ;;
esac

if [ ! -x "$compiler" ] || [ ! -x "$zig" ] || [ ! -f "$source_file" ]; then
    printf 'Expected an extracted native SDK and a bundled compiler source.\n' >&2
    exit 2
fi

mkdir -- "$2"
output=$(CDPATH='' cd -- "$2" && pwd -P)
export ZIG_LOCAL_CACHE_DIR="$output/zig-local"
export ZIG_GLOBAL_CACHE_DIR="$output/zig-global"

# The old SDK can generate different C after a backend change. Compare the
# emissions from the newly built compiler stages, starting with stage 1.
for stage in 0 1 2 3; do
    emission="$output/compiler$stage.c"
    "$compiler" --stdlib-root "$repo_root/stdlib" --emit-c-mir "$source_file" "$emission"
    if [ "$stage" -lt 3 ]; then
        next="$output/stage$((stage + 1))"
        "$zig" cc -target "$target" -std=c99 -O1 -mcpu=baseline -I"$repo_root" "$emission" -lc -lm -o "$next"
        compiler="$next"
    fi
done

cmp "$output/compiler1.c" "$output/compiler2.c"
cmp "$output/compiler2.c" "$output/compiler3.c"
printf 'Native bootstrap fixed point verified. Compiler: %s\n' "$compiler"
