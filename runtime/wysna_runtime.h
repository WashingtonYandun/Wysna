#ifndef WYSNA_RUNTIME_H
#define WYSNA_RUNTIME_H

#ifndef _WIN32
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#endif

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <inttypes.h>
#include <float.h>
#include <math.h>
#include <errno.h>
#include <ctype.h>
#include <time.h>
#include <stdarg.h>
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

#include "wysna_host.h"

#endif /* WYSNA_RUNTIME_H */
