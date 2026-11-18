#ifndef WYSNA_SHARED_APP_QUOTES_ADAPTER_H
#define WYSNA_SHARED_APP_QUOTES_ADAPTER_H

#include <stdint.h>

void *quotes_db_open(void);
int32_t quotes_db_is_open(void *handle);
int64_t quotes_db_insert(void *handle, int64_t quantity, int64_t unit_price_cents, int64_t total_cents);
int32_t quotes_db_get(void *handle, int64_t id);
int64_t quotes_db_fetched_quantity(void *handle);
int64_t quotes_db_fetched_unit_price(void *handle);
int64_t quotes_db_fetched_total(void *handle);
int64_t quotes_db_count(void *handle);
int32_t quotes_db_close(void *handle);

#endif
