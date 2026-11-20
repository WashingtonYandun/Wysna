#ifndef WYSNA_OPS_SQLITE_H
#define WYSNA_OPS_SQLITE_H

#include <stdint.h>

void *ops_db_open(void);
int32_t ops_db_is_open(void *handle);
int32_t ops_db_record(void *handle, int64_t event_id, int64_t status_code, int64_t latency_ms, int64_t risk_points);
int64_t ops_db_count(void *handle);
int64_t ops_db_critical_count(void *handle);
int32_t ops_db_close(void *handle);

#endif
