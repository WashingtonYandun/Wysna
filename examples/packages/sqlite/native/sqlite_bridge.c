#include "sqlite_bridge.h"

#include <sqlite3.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char* data;
    size_t length;
    size_t capacity;
} WsByteBuf;

typedef struct {
    sqlite3* db;
    int last_code;
    char last_error[512];
} WsSqliteDb;

typedef struct {
    WsSqliteDb* owner;
    sqlite3_stmt* stmt;
} WsSqliteStmt;

static void ws_set_error(WsSqliteDb* db, int code, const char* msg) {
    if (!db) return;
    db->last_code = code;
    if (!msg) {
        const char* errmsg = db->db ? sqlite3_errmsg(db->db) : sqlite3_errstr(code);
        msg = errmsg ? errmsg : "sqlite error";
    }
    size_t n = strlen(msg);
    if (n >= sizeof(db->last_error)) n = sizeof(db->last_error) - 1;
    memcpy(db->last_error, msg, n);
    db->last_error[n] = '\0';
}

void* ws_buf_new(void) {
    return calloc(1, sizeof(WsByteBuf));
}

void ws_buf_clear(void* ptr) {
    WsByteBuf* b = (WsByteBuf*)ptr;
    if (b) b->length = 0;
}

int32_t ws_buf_push(void* ptr, int32_t byte) {
    WsByteBuf* b = (WsByteBuf*)ptr;
    if (!b || byte < 0 || byte > 255) return -1;
    if (b->length + 2 > b->capacity) {
        size_t next_cap = b->capacity ? b->capacity * 2 : 64;
        if (next_cap > (size_t)(16 * 1024 * 1024)) return -1;
        char* next = (char*)realloc(b->data, next_cap);
        if (!next) return -1;
        b->data = next;
        b->capacity = next_cap;
    }
    b->data[b->length++] = (char)(unsigned char)byte;
    b->data[b->length] = '\0';
    return 0;
}

int64_t ws_buf_len(void* ptr) {
    WsByteBuf* b = (WsByteBuf*)ptr;
    return b ? (int64_t)b->length : -1;
}

int32_t ws_buf_get(void* ptr, int64_t index) {
    WsByteBuf* b = (WsByteBuf*)ptr;
    if (!b || index < 0 || (size_t)index >= b->length) return -1;
    return (int32_t)(unsigned char)b->data[index];
}

void ws_buf_free(void* ptr) {
    WsByteBuf* b = (WsByteBuf*)ptr;
    if (!b) return;
    free(b->data);
    free(b);
}

static const char* ws_buf_cstr(void* ptr) {
    WsByteBuf* b = (WsByteBuf*)ptr;
    if (!b || !b->data) return "";
    return b->data;
}

void* ws_sqlite_open(void* path_buf) {
    WsSqliteDb* handle = (WsSqliteDb*)calloc(1, sizeof(WsSqliteDb));
    if (!handle) return NULL;
    const char* path = ws_buf_cstr(path_buf);
    int rc = sqlite3_open_v2(
        path[0] ? path : ":memory:",
        &handle->db,
        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE,
        NULL
    );
    if (rc != SQLITE_OK) {
        ws_set_error(handle, rc, NULL);
        if (handle->db) {
            sqlite3_close(handle->db);
            handle->db = NULL;
        }
        return handle;
    }
    sqlite3_busy_timeout(handle->db, 3000);
    handle->last_code = SQLITE_OK;
    return handle;
}

int32_t ws_sqlite_close(void* db_handle) {
    WsSqliteDb* handle = (WsSqliteDb*)db_handle;
    if (!handle) return 0;
    int rc = SQLITE_OK;
    if (handle->db) {
        rc = sqlite3_close(handle->db);
        handle->db = NULL;
    }
    free(handle);
    return rc == SQLITE_OK ? 0 : rc;
}

int32_t ws_sqlite_exec(void* db_handle, void* sql_buf) {
    WsSqliteDb* handle = (WsSqliteDb*)db_handle;
    if (!handle || !handle->db) return SQLITE_MISUSE;
    char* errmsg = NULL;
    int rc = sqlite3_exec(handle->db, ws_buf_cstr(sql_buf), NULL, NULL, &errmsg);
    if (rc != SQLITE_OK) {
        ws_set_error(handle, rc, errmsg);
        if (errmsg) sqlite3_free(errmsg);
    } else {
        handle->last_code = SQLITE_OK;
        handle->last_error[0] = '\0';
    }
    return rc;
}

void* ws_sqlite_prepare(void* db_handle, void* sql_buf) {
    WsSqliteDb* handle = (WsSqliteDb*)db_handle;
    if (!handle || !handle->db) return NULL;
    sqlite3_stmt* stmt = NULL;
    WsByteBuf* b = (WsByteBuf*)sql_buf;
    int sql_len = b ? (int)b->length : 0;
    int rc = sqlite3_prepare_v2(handle->db, ws_buf_cstr(sql_buf), sql_len, &stmt, NULL);
    if (rc != SQLITE_OK || !stmt) {
        ws_set_error(handle, rc, NULL);
        if (stmt) sqlite3_finalize(stmt);
        return NULL;
    }
    WsSqliteStmt* wrapped = (WsSqliteStmt*)calloc(1, sizeof(WsSqliteStmt));
    if (!wrapped) {
        sqlite3_finalize(stmt);
        return NULL;
    }
    handle->last_code = SQLITE_OK;
    handle->last_error[0] = '\0';
    wrapped->owner = handle;
    wrapped->stmt = stmt;
    return wrapped;
}

int32_t ws_sqlite_bind_int(void* stmt_handle, int32_t index, int64_t value) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    if (!wrapped || !wrapped->stmt) return SQLITE_MISUSE;
    int rc = sqlite3_bind_int64(wrapped->stmt, index, (sqlite3_int64)value);
    if (rc != SQLITE_OK) ws_set_error(wrapped->owner, rc, NULL);
    return rc;
}

int32_t ws_sqlite_bind_text(void* stmt_handle, int32_t index, void* text_buf) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    WsByteBuf* b = (WsByteBuf*)text_buf;
    if (!wrapped || !wrapped->stmt || !b) return SQLITE_MISUSE;
    int rc = sqlite3_bind_text(wrapped->stmt, index, ws_buf_cstr(text_buf), (int)b->length, SQLITE_TRANSIENT);
    if (rc != SQLITE_OK) ws_set_error(wrapped->owner, rc, NULL);
    return rc;
}

int32_t ws_sqlite_step(void* stmt_handle) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    if (!wrapped || !wrapped->stmt) return -1;
    int rc = sqlite3_step(wrapped->stmt);
    if (rc == SQLITE_ROW) return 1;
    if (rc == SQLITE_DONE) return 0;
    ws_set_error(wrapped->owner, rc, NULL);
    return -1;
}

int32_t ws_sqlite_reset(void* stmt_handle) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    if (!wrapped || !wrapped->stmt) return SQLITE_MISUSE;
    int rc = sqlite3_reset(wrapped->stmt);
    sqlite3_clear_bindings(wrapped->stmt);
    return rc;
}

int64_t ws_sqlite_column_int(void* stmt_handle, int32_t col) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    if (!wrapped || !wrapped->stmt) return 0;
    return (int64_t)sqlite3_column_int64(wrapped->stmt, col);
}

int32_t ws_sqlite_column_text(void* stmt_handle, int32_t col, void* out_buf) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    if (!wrapped || !wrapped->stmt || !out_buf) return -1;
    ws_buf_clear(out_buf);
    const unsigned char* text = sqlite3_column_text(wrapped->stmt, col);
    int n = sqlite3_column_bytes(wrapped->stmt, col);
    if (!text || n <= 0) return 0;
    for (int i = 0; i < n; i++) {
        if (ws_buf_push(out_buf, (int32_t)text[i]) != 0) return -1;
    }
    return n;
}

int32_t ws_sqlite_finalize(void* stmt_handle) {
    WsSqliteStmt* wrapped = (WsSqliteStmt*)stmt_handle;
    if (!wrapped) return 0;
    if (wrapped->stmt) {
        (void)sqlite3_finalize(wrapped->stmt);
        wrapped->stmt = NULL;
    }
    free(wrapped);
    return 0;
}

int32_t ws_sqlite_last_code(void* db_handle) {
    WsSqliteDb* handle = (WsSqliteDb*)db_handle;
    return handle ? handle->last_code : SQLITE_MISUSE;
}

void ws_sqlite_last_error(void* db_handle, void* out_buf) {
    WsSqliteDb* handle = (WsSqliteDb*)db_handle;
    if (!out_buf) return;
    ws_buf_clear(out_buf);
    const char* msg = (handle && handle->last_error[0]) ? handle->last_error : "sqlite error";
    for (size_t i = 0; msg[i] != '\0'; i++) {
        if (ws_buf_push(out_buf, (int32_t)(unsigned char)msg[i]) != 0) break;
    }
}
