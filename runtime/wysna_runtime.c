/*
 * Wysna Shared Runtime Translation Unit
 * Compiles the runtime context definitions for multi-object / modular linking.
 */
#define WYSNA_SHARED_RUNTIME
#include "wysna_runtime.h"

WysnaRuntimeContext wysna_runtime_context = {0};
WysnaHostContext wysna_host_context = {NULL, 1, false, 0, 0};
bool wysna_http_curl_initialized = false;
