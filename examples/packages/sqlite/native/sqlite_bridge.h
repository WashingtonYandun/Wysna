#ifndef WYSNA_SQLITE_BRIDGE_H
#define WYSNA_SQLITE_BRIDGE_H

#include <stdint.h>

void* ws_buf_new(void);
void ws_buf_clear(void* buf);
int32_t ws_buf_push(void* buf, int32_t byte);
int64_t ws_buf_len(void* buf);
int32_t ws_buf_get(void* buf, int64_t index);
void ws_buf_free(void* buf);

void* ws_sqlite_open(void* path_buf);
int32_t ws_sqlite_close(void* db_handle);
int32_t ws_sqlite_exec(void* db_handle, void* sql_buf);
void* ws_sqlite_prepare(void* db_handle, void* sql_buf);
int32_t ws_sqlite_bind_int(void* stmt_handle, int32_t index, int64_t value);
int32_t ws_sqlite_bind_text(void* stmt_handle, int32_t index, void* text_buf);
int32_t ws_sqlite_step(void* stmt_handle);
int32_t ws_sqlite_reset(void* stmt_handle);
int64_t ws_sqlite_column_int(void* stmt_handle, int32_t col);
int32_t ws_sqlite_column_text(void* stmt_handle, int32_t col, void* out_buf);
int32_t ws_sqlite_finalize(void* stmt_handle);
int32_t ws_sqlite_last_code(void* db_handle);
void ws_sqlite_last_error(void* db_handle, void* out_buf);

#endif
