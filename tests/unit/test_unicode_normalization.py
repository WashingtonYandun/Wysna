"""Native NFC/NFD must agree with the reference implementation for the pinned Unicode version."""
import random
import re
import subprocess
import unicodedata
from pathlib import Path

import pytest

from common.Paths import get_repo_root
from common.Platform import compiler_command

ROOT = Path(get_repo_root())
TABLES = (ROOT / 'runtime' / 'wysna_unicode_tables.h').read_text()
TABLE_VERSION = re.search(r'WYSNA_UNICODE_VERSION "([^"]+)"', TABLE_VERSION_SOURCE := TABLES).group(1)

HARNESS = r'''#include "runtime/wysna_runtime.h"
#include "runtime/wysna_host.h"
#include <stdio.h>

static size_t unhex(const char* text, uint8_t* out, size_t cap) {
    size_t n = 0;
    while (text[0] && text[1] && text[0] != '\t' && text[0] != '\n' && n < cap) {
        unsigned value;
        sscanf(text, "%2x", &value);
        out[n++] = (uint8_t)value;
        text += 2;
    }
    return n;
}

static void* make(const uint8_t* data, size_t n) {
    void* handle = wysna_host_buffer_new();
    for (size_t i = 0; i < n; i++) wysna_host_buffer_append(handle, data[i]);
    return handle;
}

static int same(void* handle, const uint8_t* expected, size_t n) {
    WysnaHostEntry* entry = wysna_host_find(handle, 1);
    return entry && entry->length == n && (n == 0 || memcmp(entry->data, expected, n) == 0);
}

int main(int argc, char** argv) {
    wysna_init_args(argc, argv);
    FILE* file = fopen(argv[1], "r");
    if (!file) return 2;
    static char line[65536];
    long checked = 0;
    while (fgets(line, sizeof(line), file)) {
        static uint8_t input[16384], nfc[16384], nfd[16384];
        char* first = line;
        char* second = strchr(first, '\t');
        if (!second) continue;
        *second++ = 0;
        char* third = strchr(second, '\t');
        if (!third) continue;
        *third++ = 0;
        size_t in_n = unhex(first, input, sizeof(input));
        size_t nfc_n = unhex(second, nfc, sizeof(nfc));
        size_t nfd_n = unhex(third, nfd, sizeof(nfd));
        void* in_handle = make(input, in_n);
        void* out_c = wysna_host_buffer_new();
        void* out_d = wysna_host_buffer_new();
        if (wysna_host_unicode_normalize(in_handle, 0, out_c) != 0 || !same(out_c, nfc, nfc_n)) {
            fprintf(stderr, "NFC mismatch on case %ld\n", checked);
            return 1;
        }
        if (wysna_host_unicode_normalize(in_handle, 1, out_d) != 0 || !same(out_d, nfd, nfd_n)) {
            fprintf(stderr, "NFD mismatch on case %ld\n", checked);
            return 1;
        }
        wysna_host_close(in_handle); wysna_host_close(out_c); wysna_host_close(out_d);
        checked++;
    }
    /* Invalid UTF-8 must be rejected, not normalized. */
    static const uint8_t bad[] = {0xC0, 0xAF};
    void* bad_in = make(bad, sizeof(bad));
    void* bad_out = wysna_host_buffer_new();
    if (wysna_host_unicode_normalize(bad_in, 0, bad_out) == 0) return 3;
    static const uint8_t surrogate[] = {0xED, 0xA0, 0x80};
    if (wysna_host_unicode_normalize(make(surrogate, 3), 1, wysna_host_buffer_new()) == 0) return 4;
    printf("%ld\n", checked);
    return 0;
}
'''


def hexed(text):
    return text.encode('utf-8').hex()


def cases():
    items = []
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        items.append(chr(cp))
    rng = random.Random(8439)
    pools = [chr(cp) for cp in range(0x300, 0x370)] + list('aeiousnyAEIOUCSNZ\u00e9\u1e69\u0f73\u1100\u1161\u11a8\uac00\uac01')
    pools += [chr(cp) for cp in (0x0F71, 0x0F72, 0x0F74, 0x1B35, 0x3099, 0x309A, 0x0654, 0x0655, 0x064B, 0x0651)]
    for _ in range(30000):
        items.append(''.join(rng.choice(pools) for _ in range(rng.randint(1, 9))))
    return items


@pytest.mark.skipif(unicodedata.unidata_version != TABLE_VERSION,
                    reason=f'tables pinned to Unicode {TABLE_VERSION}; interpreter has {unicodedata.unidata_version}')
def test_native_normalization_matches_unicodedata_exhaustively(tmp_path):
    data = tmp_path / 'cases.tsv'
    with data.open('w') as handle:
        for text in cases():
            handle.write('\t'.join((hexed(text), hexed(unicodedata.normalize('NFC', text)),
                                    hexed(unicodedata.normalize('NFD', text)))) + '\n')
    source = tmp_path / 'harness.c'
    source.write_text(HARNESS)
    binary = tmp_path / 'harness'
    compiler = compiler_command()
    build = subprocess.run([*compiler, '-std=c99', '-O1', '-fsanitize=address,undefined', f'-I{ROOT}',
                            str(source), '-lm', '-o', str(binary)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary), str(data)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert int(run.stdout.strip()) > 1_000_000


def test_table_generator_is_deterministic():
    if unicodedata.unidata_version != TABLE_VERSION:
        pytest.skip('generator output depends on the interpreter Unicode version')
    result = subprocess.run(['python3', str(ROOT / 'tools' / 'gen_unicode_tables.py'), '--check'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
