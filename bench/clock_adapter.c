/* Measurement-only monotonic clock; identical adapter for both native backends. */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdint.h>
#include <time.h>
#include <stdlib.h>
int64_t wysna_benchmark_nanoseconds(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) abort();
    return (int64_t)value.tv_sec * INT64_C(1000000000) + value.tv_nsec;
}
