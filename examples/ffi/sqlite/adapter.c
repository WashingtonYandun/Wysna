#include "adapter.h"
#include <errno.h>
#include <sqlite3.h>
#include <stdlib.h>
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <wchar.h>
#endif

typedef struct {
    sqlite3 *database;
    sqlite3_stmt *insert;
    int status;
} PilotDatabase;

/* Single-threaded resource accounting for the pilot's C-owned handles. */
static int32_t live_resources;

void *pilot_open(void) {
    PilotDatabase *handle = calloc(1, sizeof(*handle));
    if (!handle) return NULL;
    live_resources++;
    char *encoded_path = NULL;
#ifdef _WIN32
    const wchar_t *wide_path = _wgetenv(L"WYSNA_SQLITE_DB");
    if (wide_path) {
        int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide_path, -1, NULL, 0, NULL, NULL);
        if (!bytes) { handle->status = SQLITE_CANTOPEN; return handle; }
        encoded_path = malloc((size_t)bytes);
        if (!encoded_path) { handle->status = SQLITE_NOMEM; return handle; }
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide_path, -1, encoded_path, bytes, NULL, NULL)) {
            free(encoded_path);
            handle->status = SQLITE_CANTOPEN;
            return handle;
        }
    }
    const char *path = encoded_path;
#else
    const char *path = getenv("WYSNA_SQLITE_DB");
#endif
    handle->status = sqlite3_open(path ? path : ":memory:", &handle->database);
    free(encoded_path);
    if (handle->database) live_resources++;
    if (handle->status == SQLITE_OK) {
        handle->status = sqlite3_exec(handle->database,
            "CREATE TABLE IF NOT EXISTS measurements(id INTEGER PRIMARY KEY, value INTEGER NOT NULL CHECK(value >= 0));"
            "CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "row_count INTEGER NOT NULL CHECK(row_count > 0), input_bytes INTEGER NOT NULL CHECK(input_bytes > 0))",
            NULL, NULL, NULL);
    }
    if (handle->status == SQLITE_OK) {
        handle->status = sqlite3_prepare_v2(handle->database,
            "INSERT INTO measurements(id, value) VALUES(?1, ?2)", -1, &handle->insert, NULL);
        if (handle->insert) live_resources++;
    }
    return handle;
}

bool pilot_is_open(void *pointer) {
    PilotDatabase *handle = pointer;
    return handle && handle->database && handle->insert && handle->status == SQLITE_OK;
}

int32_t pilot_status(void *pointer) {
    PilotDatabase *handle = pointer;
    return handle ? handle->status : SQLITE_NOMEM;
}

static int execute(PilotDatabase *handle, const char *sql) {
    if (!handle || !handle->database) return SQLITE_MISUSE;
    handle->status = sqlite3_exec(handle->database, sql, NULL, NULL, NULL);
    return handle->status;
}

int32_t pilot_begin(void *handle) { return execute(handle, "BEGIN"); }
int32_t pilot_commit(void *handle) { return execute(handle, "COMMIT"); }
int32_t pilot_rollback(void *handle) { return execute(handle, "ROLLBACK"); }

int32_t pilot_record_import(void *pointer, int64_t rows, int64_t input_bytes) {
    PilotDatabase *handle = pointer;
    if (!handle || !handle->database || sqlite3_get_autocommit(handle->database) || rows <= 0 || input_bytes <= 0)
        return SQLITE_MISUSE;
    sqlite3_stmt *statement = NULL;
    int status = sqlite3_prepare_v2(handle->database,
        "INSERT INTO imports(row_count, input_bytes) VALUES(?1, ?2)", -1, &statement, NULL);
    if (statement) live_resources++;
    if (status == SQLITE_OK) status = sqlite3_bind_int64(statement, 1, rows);
    if (status == SQLITE_OK) status = sqlite3_bind_int64(statement, 2, input_bytes);
    if (status == SQLITE_OK) status = sqlite3_step(statement);
    if (status == SQLITE_DONE) status = SQLITE_OK;
    if (statement) {
        int finalized = sqlite3_finalize(statement);
        live_resources--;
        if (status == SQLITE_OK) status = finalized;
    }
    handle->status = status;
    return status;
}

int32_t pilot_insert(void *pointer, int64_t id, int64_t value) {
    PilotDatabase *handle = pointer;
    if (!handle || !handle->database || !handle->insert) return SQLITE_MISUSE;
    /* Reset reports the previous step error; the statement is reset either way. */
    sqlite3_reset(handle->insert);
    sqlite3_clear_bindings(handle->insert);
    int status = sqlite3_bind_int64(handle->insert, 1, id);
    if (status == SQLITE_OK) status = sqlite3_bind_int64(handle->insert, 2, value);
    if (status == SQLITE_OK) status = sqlite3_step(handle->insert);
    handle->status = status == SQLITE_DONE ? SQLITE_OK : status;
    sqlite3_reset(handle->insert);
    return handle->status;
}

static int64_t scalar(PilotDatabase *handle, const char *sql) {
    if (!handle || !handle->database) return -1;
    sqlite3_stmt *statement = NULL;
    int status = sqlite3_prepare_v2(handle->database, sql, -1, &statement, NULL);
    if (statement) live_resources++;
    int64_t value = -1;
    if (status == SQLITE_OK) {
        status = sqlite3_step(statement);
        if (status == SQLITE_ROW) {
            value = sqlite3_column_int64(statement, 0);
            status = SQLITE_OK;
        }
    }
    if (statement) {
        int finalized = sqlite3_finalize(statement);
        live_resources--;
        if (status == SQLITE_OK) status = finalized;
    }
    handle->status = status;
    return status == SQLITE_OK ? value : -1;
}

int64_t pilot_import_count(void *handle) { return scalar(handle, "SELECT count(*) FROM imports"); }

int64_t pilot_count(void *handle) { return scalar(handle, "SELECT count(*) FROM measurements"); }
int64_t pilot_total(void *handle) { return scalar(handle, "SELECT coalesce(sum(value), 0) FROM measurements"); }

int32_t pilot_close(void *pointer) {
    PilotDatabase *handle = pointer;
    if (!handle) return SQLITE_OK;
    if (handle->insert) {
        sqlite3_finalize(handle->insert);
        handle->insert = NULL;
        live_resources--;
    }
    if (handle->database) {
        int status = sqlite3_close(handle->database);
        if (status != SQLITE_OK) return status;
        handle->database = NULL;
        live_resources--;
    }
    return SQLITE_OK;
}

int32_t pilot_dispose(void *pointer) {
    if (!pointer) return SQLITE_OK;
    int status = pilot_close(pointer);
    if (status != SQLITE_OK) return status;
    free(pointer);
    live_resources--;
    return SQLITE_OK;
}

int32_t pilot_live_resources(void) { return live_resources; }
int32_t pilot_version(void) { return sqlite3_libversion_number(); }

int64_t pilot_rows(void) {
    const char *text = getenv("WYSNA_SQLITE_ROWS");
    if (!text) return 1000;
    char *end = NULL;
    errno = 0;
    long long rows = strtoll(text, &end, 10);
    return !errno && end != text && *end == '\0' && rows > 0 && rows <= 100000 ? rows : -1;
}
