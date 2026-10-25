#include "sqlite_adapter.h"

#include <sqlite3.h>
#include <stdlib.h>

typedef struct {
    sqlite3 *database;
    sqlite3_stmt *insert_measurement;
} ServiceDatabase;

void *service_db_open(void) {
    const char *path = getenv("WYSNA_SERVICE_DATABASE");
    if (!path || !path[0]) return NULL;

    ServiceDatabase *handle = calloc(1, sizeof(*handle));
    if (!handle) return NULL;
    int status = sqlite3_open_v2(path, &handle->database,
        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, NULL);
    if (status == SQLITE_OK) status = sqlite3_busy_timeout(handle->database, 5000);
    if (status == SQLITE_OK) {
        status = sqlite3_exec(handle->database,
            "CREATE TABLE IF NOT EXISTS measurements("
            "id INTEGER PRIMARY KEY, value INTEGER NOT NULL CHECK(value >= 0))",
            NULL, NULL, NULL);
    }
    if (status == SQLITE_OK) {
        status = sqlite3_prepare_v2(handle->database,
            "INSERT INTO measurements(id, value) VALUES(?1, ?2)",
            -1, &handle->insert_measurement, NULL);
    }
    if (status != SQLITE_OK) {
        if (handle->insert_measurement) sqlite3_finalize(handle->insert_measurement);
        if (handle->database) sqlite3_close(handle->database);
        free(handle);
        return NULL;
    }
    return handle;
}

static ServiceDatabase *database(void *pointer) {
    return (ServiceDatabase *)pointer;
}

int32_t service_db_is_open(void *pointer) {
    ServiceDatabase *handle = database(pointer);
    return handle && handle->database && handle->insert_measurement;
}

static int32_t execute(ServiceDatabase *handle, const char *sql) {
    if (!handle || !handle->database) return SQLITE_MISUSE;
    return sqlite3_exec(handle->database, sql, NULL, NULL, NULL);
}

int32_t service_db_begin(void *pointer) {
    return execute(database(pointer), "BEGIN IMMEDIATE");
}

int32_t service_db_insert(void *pointer, int64_t id, int64_t value) {
    ServiceDatabase *handle = database(pointer);
    if (!handle || !handle->insert_measurement) return SQLITE_MISUSE;
    sqlite3_reset(handle->insert_measurement);
    sqlite3_clear_bindings(handle->insert_measurement);
    int status = sqlite3_bind_int64(handle->insert_measurement, 1, id);
    if (status == SQLITE_OK) status = sqlite3_bind_int64(handle->insert_measurement, 2, value);
    if (status == SQLITE_OK) status = sqlite3_step(handle->insert_measurement);
    if (status == SQLITE_DONE) status = SQLITE_OK;
    sqlite3_reset(handle->insert_measurement);
    return status;
}

int32_t service_db_commit(void *pointer) {
    return execute(database(pointer), "COMMIT");
}

int32_t service_db_rollback(void *pointer) {
    return execute(database(pointer), "ROLLBACK");
}

int64_t service_db_count(void *pointer) {
    ServiceDatabase *handle = database(pointer);
    if (!handle || !handle->database) return -1;
    sqlite3_stmt *statement = NULL;
    int status = sqlite3_prepare_v2(handle->database,
        "SELECT count(*) FROM measurements", -1, &statement, NULL);
    int64_t result = -1;
    if (status == SQLITE_OK && sqlite3_step(statement) == SQLITE_ROW) {
        result = sqlite3_column_int64(statement, 0);
    }
    if (statement) sqlite3_finalize(statement);
    return result;
}

int32_t service_db_close(void *pointer) {
    ServiceDatabase *handle = database(pointer);
    if (!handle) return SQLITE_OK;
    int status = SQLITE_OK;
    if (handle->insert_measurement) {
        status = sqlite3_finalize(handle->insert_measurement);
        handle->insert_measurement = NULL;
    }
    if (handle->database) {
        int close_status = sqlite3_close(handle->database);
        if (status == SQLITE_OK) status = close_status;
        if (close_status == SQLITE_OK) handle->database = NULL;
    }
    if (status == SQLITE_OK) free(handle);
    return status;
}
