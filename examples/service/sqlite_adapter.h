#ifndef WYSNA_SERVICE_SQLITE_ADAPTER_H
#define WYSNA_SERVICE_SQLITE_ADAPTER_H

#include <stdint.h>

void *service_db_open(void);
int32_t service_db_is_open(void *handle);
int32_t service_db_begin(void *handle);
int32_t service_db_insert(void *handle, int64_t id, int64_t value);
int32_t service_db_commit(void *handle);
int32_t service_db_rollback(void *handle);
int64_t service_db_count(void *handle);
int32_t service_db_close(void *handle);

#endif
