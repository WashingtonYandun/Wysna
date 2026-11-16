/* Benchmark W07-c: L1 cache-density comparison between 8-byte WVal64 and 32-byte WVal. */
#include "runtime/wysna_runtime.h"
#include <stdio.h>
#include <stdint.h>
#include <time.h>

static uint64_t bench_ns(void) {
#ifdef _WIN32
    LARGE_INTEGER freq, counter;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&counter);
    return (uint64_t)(((double)counter.QuadPart * 1e9) / (double)freq.QuadPart);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
#endif
}

int main(void) {
    enum { N = 131072, ROUNDS = 128 };
    WVal* wide = (WVal*)calloc(N, sizeof(WVal));
    WVal64* compact = (WVal64*)calloc(N, sizeof(WVal64));
    if (!wide || !compact) return 1;

    for (size_t i = 0; i < N; i++) {
        wide[i] = wysna_int((int64_t)i);
        compact[i] = wysna_val64_pack(wide[i]);
    }

    uint64_t t0 = bench_ns();
    int64_t sum_wide = 0;
    for (int r = 0; r < ROUNDS; r++) {
        for (size_t i = 0; i < N; i++) {
            sum_wide += wide[i].as.i;
        }
    }
    uint64_t wide_ns = bench_ns() - t0;

    uint64_t t1 = bench_ns();
    int64_t sum_compact = 0;
    for (int r = 0; r < ROUNDS; r++) {
        for (size_t i = 0; i < N; i++) {
            sum_compact += wysna_val64_unpack(compact[i]).as.i;
        }
    }
    uint64_t compact_ns = bench_ns() - t1;

    printf("sizeof(WVal)=%zu sizeof(WVal64)=%zu wide_ms=%.2f compact_ms=%.2f speedup=%.2fx sum_match=%d\n",
           sizeof(WVal), sizeof(WVal64),
           wide_ns / 1e6, compact_ns / 1e6,
           compact_ns > 0 ? (double)wide_ns / (double)compact_ns : 1.0,
           sum_wide == sum_compact);
    free(wide);
    free(compact);
    wysna_runtime_cleanup();
    return sum_wide == sum_compact ? 0 : 1;
}
