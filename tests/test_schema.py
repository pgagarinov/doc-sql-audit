"""Verify database schema matches expectations."""

from doc_sql_audit.constants import N_CONTRACTS_TOTAL, N_PROTOCOLS


def test_tables_exist(cursor):
    cursor.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [row[0] for row in cursor.fetchall()]
    assert "contracts" in tables
    assert "protocols" in tables
    assert "approvals" in tables


def test_contracts_columns(cursor):
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'contracts'
        ORDER BY ordinal_position
    """)
    cols = [row[0] for row in cursor.fetchall()]
    assert "contract_id" in cols
    assert "contract_number" in cols
    assert "amount" in cols
    assert "supplier" in cols
    assert "contract_type" in cols
    assert "protocol_id" in cols
    assert "subject" in cols


def test_protocols_columns(cursor):
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'protocols'
        ORDER BY ordinal_position
    """)
    cols = [row[0] for row in cursor.fetchall()]
    assert "protocol_id" in cols
    assert "protocol_number" in cols
    assert "protocol_date" in cols
    assert "approved_amount" in cols
    assert "protocol_text" in cols


def test_approvals_columns(cursor):
    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'approvals'
        ORDER BY ordinal_position
    """)
    cols = [row[0] for row in cursor.fetchall()]
    assert "approval_id" in cols
    assert "contract_id" in cols
    assert "fin_director" in cols
    assert "lawyer" in cols
    assert "security" in cols
    assert "procurement_head" in cols


def test_trgm_extension_enabled(cursor):
    cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
    assert cursor.fetchone() is not None


def test_trgm_index_exists(cursor):
    cursor.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'contracts'
        AND indexname = 'idx_contracts_subject_trgm'
    """)
    assert cursor.fetchone() is not None


def test_contracts_row_count(cursor):
    cursor.execute("SELECT count(*) FROM contracts")
    assert cursor.fetchone()[0] == N_CONTRACTS_TOTAL


def test_protocols_row_count(cursor):
    cursor.execute("SELECT count(*) FROM protocols")
    assert cursor.fetchone()[0] == N_PROTOCOLS


def test_approvals_row_count(cursor):
    cursor.execute("SELECT count(*) FROM approvals")
    assert cursor.fetchone()[0] == N_CONTRACTS_TOTAL
