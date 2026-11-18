#include "quotes_adapter.h"

#include <sqlite3.h>
#include <stdlib.h>

typedef struct {
    sqlite3 *database;
    sqlite3_stmt *insert_quote;
    sqlite3_stmt *select_quote;
    int64_t fetched_quantity;
    int64_t fetched_unit_price;
    int64_t fetched_total;
} QuotesDatabase;

void *quotes_db_open(void) {
    const char *path = getenv("WYSNA_SERVICE_DATABASE");
    if (!path || !path[0]) return NULL;

    QuotesDatabase *handle = calloc(1, sizeof(*handle));
    if (!handle) return NULL;
    int status = sqlite3_open_v2(
        path,
        &handle->database,
        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
        NULL);
    if (status == SQLITE_OK) status = sqlite3_busy_timeout(handle->database, 5000);
    if (status == SQLITE_OK) {
        status = sqlite3_exec(
            handle->database,
            "CREATE TABLE IF NOT EXISTS quotes("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "quantity INTEGER NOT NULL CHECK(quantity >= 1 AND quantity <= 1000),"
            "unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0 AND unit_price_cents <= 100000000),"
            "total_cents INTEGER NOT NULL CHECK(total_cents >= 0))",
            NULL, NULL, NULL);
    }
    if (status == SQLITE_OK) {
        status = sqlite3_prepare_v2(
            handle->database,
            "INSERT INTO quotes(quantity, unit_price_cents, total_cents) VALUES(?1, ?2, ?3)",
            -1, &handle->insert_quote, NULL);
    }
    if (status == SQLITE_OK) {
        status = sqlite3_prepare_v2(
            handle->database,
            "SELECT quantity, unit_price_cents, total_cents FROM quotes WHERE id = ?1",
            -1, &handle->select_quote, NULL);
    }
    if (status != SQLITE_OK) {
        if (handle->select_quote) sqlite3_finalize(handle->select_quote);
        if (handle->insert_quote) sqlite3_finalize(handle->insert_quote);
        if (handle->database) sqlite3_close(handle->database);
        free(handle);
        return NULL;
    }
    return handle;
}

static QuotesDatabase *database(void *pointer) {
    return (QuotesDatabase *)pointer;
}

int32_t quotes_db_is_open(void *pointer) {
    QuotesDatabase *handle = database(pointer);
    return handle && handle->database && handle->insert_quote && handle->select_quote;
}

int64_t quotes_db_insert(void *pointer, int64_t quantity, int64_t unit_price_cents, int64_t total_cents) {
    QuotesDatabase *handle = database(pointer);
    if (!handle || !handle->database || !handle->insert_quote) return -1;
    if (sqlite3_exec(handle->database, "BEGIN IMMEDIATE", NULL, NULL, NULL) != SQLITE_OK) {
        return -1;
    }
    sqlite3_reset(handle->insert_quote);
    sqlite3_clear_bindings(handle->insert_quote);
    int status = sqlite3_bind_int64(handle->insert_quote, 1, quantity);
    if (status == SQLITE_OK) status = sqlite3_bind_int64(handle->insert_quote, 2, unit_price_cents);
    if (status == SQLITE_OK) status = sqlite3_bind_int64(handle->insert_quote, 3, total_cents);
    if (status == SQLITE_OK) status = sqlite3_step(handle->insert_quote);
    sqlite3_reset(handle->insert_quote);
    if (status != SQLITE_DONE) {
        sqlite3_exec(handle->database, "ROLLBACK", NULL, NULL, NULL);
        return -1;
    }
    int64_t quote_id = sqlite3_last_insert_rowid(handle->database);
    if (sqlite3_exec(handle->database, "COMMIT", NULL, NULL, NULL) != SQLITE_OK) {
        sqlite3_exec(handle->database, "ROLLBACK", NULL, NULL, NULL);
        return -1;
    }
    return quote_id;
}

int32_t quotes_db_get(void *pointer, int64_t id) {
    QuotesDatabase *handle = database(pointer);
    if (!handle || !handle->select_quote) return -1;
    sqlite3_reset(handle->select_quote);
    sqlite3_clear_bindings(handle->select_quote);
    int status = sqlite3_bind_int64(handle->select_quote, 1, id);
    int32_t found = -1;
    if (status == SQLITE_OK) {
        int step_rc = sqlite3_step(handle->select_quote);
        if (step_rc == SQLITE_ROW) {
            handle->fetched_quantity = sqlite3_column_int64(handle->select_quote, 0);
            handle->fetched_unit_price = sqlite3_column_int64(handle->select_quote, 1);
            handle->fetched_total = sqlite3_column_int64(handle->select_quote, 2);
            found = 1;
        } else if (step_rc == SQLITE_DONE) {
            found = 0;
        }
    }
    sqlite3_reset(handle->select_quote);
    return found;
}

int64_t quotes_db_fetched_quantity(void *pointer) {
    QuotesDatabase *handle = database(pointer);
    return handle ? handle->fetched_quantity : -1;
}

int64_t quotes_db_fetched_unit_price(void *pointer) {
    QuotesDatabase *handle = database(pointer);
    return handle ? handle->fetched_unit_price : -1;
}

int64_t quotes_db_fetched_total(void *pointer) {
    QuotesDatabase *handle = database(pointer);
    return handle ? handle->fetched_total : -1;
}

int64_t quotes_db_count(void *pointer) {
    QuotesDatabase *handle = database(pointer);
    if (!handle || !handle->database) return -1;
    sqlite3_stmt *statement = NULL;
    int status = sqlite3_prepare_v2(handle->database, "SELECT count(*) FROM quotes", -1, &statement, NULL);
    int64_t count = -1;
    if (status == SQLITE_OK && sqlite3_step(statement) == SQLITE_ROW) {
        count = sqlite3_column_int64(statement, 0);
    }
    if (statement) sqlite3_finalize(statement);
    return count;
}

int32_t quotes_db_close(void *pointer) {
    QuotesDatabase *handle = database(pointer);
    if (!handle) return SQLITE_OK;
    int status = SQLITE_OK;
    if (handle->select_quote) {
        int finalize_rc = sqlite3_finalize(handle->select_quote);
        if (status == SQLITE_OK) status = finalize_rc;
        handle->select_quote = NULL;
    }
    if (handle->insert_quote) {
        int finalize_rc = sqlite3_finalize(handle->insert_quote);
        if (status == SQLITE_OK) status = finalize_rc;
        handle->insert_quote = NULL;
    }
    if (handle->database) {
        int close_rc = sqlite3_close(handle->database);
        if (status == SQLITE_OK) status = close_rc;
        if (close_rc == SQLITE_OK) handle->database = NULL;
    }
    if (status == SQLITE_OK) free(handle);
    return status;
}
