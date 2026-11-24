#ifndef WYSNA_RUNTIME_H
#define WYSNA_RUNTIME_H

#ifndef _WIN32
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700
#endif
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#endif

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <inttypes.h>
#include <math.h>
#include <stdarg.h>
#ifdef W_OK
#undef W_OK
#endif

#if defined(WYSNA_RUNTIME_LINKED) && !defined(WYSNA_RUNTIME_IMPL) && !defined(WYSNA_HAS_HTTP) && !defined(W_STRING_METRICS) && !defined(WYSNA_DEFAULT_MAX_STEPS) && !defined(WYSNA_DEFAULT_MAX_HEAP_BYTES) && !defined(WYSNA_SANDBOX_ENFORCED)
#include "wysna_sdk_abi.h"
#else
#include <float.h>
#include <errno.h>
#include <ctype.h>
#include <time.h>
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif
#ifdef W_OK
#undef W_OK
#endif

/* Ordered implementation domains behind the stable runtime ABI. */
#include "wysna_runtime_core.h"
#include "wysna_runtime_values.h"
#include "wysna_runtime_memory.h"
#include "wysna_runtime_equality.h"
#include "wysna_runtime_collections.h"
#include "wysna_runtime_text.h"
#include "wysna_runtime_access.h"
#include "wysna_runtime_operators.h"
#include "wysna_runtime_format.h"
#include "wysna_runtime_builtins.h"
#include "wysna_runtime_workers.h"
#include "wysna_runtime_json.h"

#include "wysna_host.h"
#endif

#endif /* WYSNA_RUNTIME_H */
