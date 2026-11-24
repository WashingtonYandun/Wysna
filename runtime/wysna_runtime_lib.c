/*
 * Wysna Precompiled Native SDK Runtime Translation Unit
 * Compiles all WYSNA_RT_FN runtime/host implementations and shared state into a single archive object.
 */
#define WYSNA_SHARED_RUNTIME
#define WYSNA_RUNTIME_IMPL
#include "wysna_runtime.h"

WysnaRuntimeContext wysna_runtime_context = {0};
WYSNA_THREAD_LOCAL WysnaThreadDiagnostics wysna_thread_diagnostics = {0};
WYSNA_THREAD_LOCAL int32_t wysna_worker_depth = 0;
WysnaHostContext wysna_host_context = {NULL, 1, false, 0, 0};
bool wysna_http_curl_initialized = false;
