#include "ops_sqlite.h"

#include <sqlite3.h>
#include <stdlib.h>

typedef struct {
    sqlite3 *db;
    sqlite3_stmt *insert_stmt;
} OpsAuditDb;

void *ops_db_open(void) {
    const char *path = getenv("WYSNA_OPS_DB");
    if (!path || !path[0]) path = ":memory:";
    OpsAuditDb *h = (OpsAuditDb *)calloc(1, sizeof(*h));
    if (!h) return NULL;
    int rc = sqlite3_open_v2(path, &h->db,
        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, NULL);
    if (rc == SQLITE_OK) {
        rc = sqlite3_exec(h->db,
            "CREATE TABLE IF NOT EXISTS audit_events("
            "id INTEGER PRIMARY KEY, status_code INTEGER NOT NULL, "
            "latency_ms INTEGER NOT NULL, risk_points INTEGER NOT NULL)",
            NULL, NULL, NULL);
    }
    if (rc == SQLITE_OK) {
        rc = sqlite3_prepare_v2(h->db,
            "INSERT OR REPLACE INTO audit_events(id, status_code, latency_ms, risk_points) VALUES(?1, ?2, ?3, ?4)",
            -1, &h->insert_stmt, NULL);
    }
    if (rc != SQLITE_OK) {
        if (h->insert_stmt) sqlite3_finalize(h->insert_stmt);
        if (h->db) sqlite3_close(h->db);
        free(h);
        return NULL;
    }
    return h;
}

int32_t ops_db_is_open(void *handle) {
    OpsAuditDb *h = (OpsAuditDb *)handle;
    return h && h->db && h->insert_stmt ? 1 : 0;
}

int32_t ops_db_record(void *handle, int64_t event_id, int64_t status_code, int64_t latency_ms, int64_t risk_points) {
    OpsAuditDb *h = (OpsAuditDb *)handle;
    if (!h || !h->insert_stmt) return SQLITE_MISUSE;
    sqlite3_reset(h->insert_stmt);
    sqlite3_clear_bindings(h->insert_stmt);
    int rc = sqlite3_bind_int64(h->insert_stmt, 1, event_id);
    if (rc == SQLITE_OK) rc = sqlite3_bind_int64(h->insert_stmt, 2, status_code);
    if (rc == SQLITE_OK) rc = sqlite3_bind_int64(h->insert_stmt, 3, latency_ms);
    if (rc == SQLITE_OK) rc = sqlite3_bind_int64(h->insert_stmt, 4, risk_points);
    if (rc == SQLITE_OK) rc = sqlite3_step(h->insert_stmt);
    if (rc == SQLITE_DONE) rc = SQLITE_OK;
    sqlite3_reset(h->insert_stmt);
    return rc;
}

int64_t ops_db_count(void *handle) {
    OpsAuditDb *h = (OpsAuditDb *)handle;
    if (!h || !h->db) return -1;
    sqlite3_stmt *stmt = NULL;
    int rc = sqlite3_prepare_v2(h->db, "SELECT count(*) FROM audit_events", -1, &stmt, NULL);
    int64_t out = -1;
    if (rc == SQLITE_OK && sqlite3_step(stmt) == SQLITE_ROW) {
        out = sqlite3_column_int64(stmt, 0);
    }
    if (stmt) sqlite3_finalize(stmt);
    return out;
}

int64_t ops_db_critical_count(void *handle) {
    OpsAuditDb *h = (OpsAuditDb *)handle;
    if (!h || !h->db) return -1;
    sqlite3_stmt *stmt = NULL;
    int rc = sqlite3_prepare_v2(h->db,
        "SELECT count(*) FROM audit_events WHERE status_code >= 500 OR latency_ms >= 1500",
        -1, &stmt, NULL);
    int64_t out = -1;
    if (rc == SQLITE_OK && sqlite3_step(stmt) == SQLITE_ROW) {
        out = sqlite3_column_int64(stmt, 0);
    }
    if (stmt) sqlite3_finalize(stmt);
    return out;
}

int32_t ops_db_close(void *handle) {
    OpsAuditDb *h = (OpsAuditDb *)handle;
    if (!h) return SQLITE_OK;
    int rc = SQLITE_OK;
    if (h->insert_stmt) {
        rc = sqlite3_finalize(h->insert_stmt);
        h->insert_stmt = NULL;
    }
    if (h->db) {
        int close_rc = sqlite3_close(h->db);
        if (rc == SQLITE_OK) rc = close_rc;
        if (close_rc == SQLITE_OK) h->db = NULL;
    }
    if (rc == SQLITE_OK) free(h);
    return rc;
}
