"""Verify audit query results against known data generation invariants.

Each test class corresponds to an audit scenario from CLAUDE.md.
Reference SQL is included to validate the business logic; in production use,
Claude generates this SQL dynamically via the MCP execute_sql tool.
"""

import pytest

from doc_sql_audit.constants import (
    NONSTANDARD_RATIO,
    LAWYER_FALSE_RATIO,
    PROTOCOL_NO_LAWYER_RATIO,
    N_CONTRACTS_TOTAL,
    N_MAIN,
    N_DS,
    N_PROTOCOLS,
)

TOLERANCE = 0.03  # allow 3% deviation from expected ratios


# ---------------------------------------------------------------------------
# Data distribution sanity checks
# ---------------------------------------------------------------------------


class TestDataDistribution:
    """Verify that generation ratios are reflected in the database."""

    def test_nonstandard_ratio(self, cursor):
        cursor.execute(
            "SELECT count(*) FROM contracts WHERE contract_type = 'нестандартный'"
        )
        actual = cursor.fetchone()[0] / N_CONTRACTS_TOTAL
        assert abs(actual - NONSTANDARD_RATIO) < TOLERANCE

    def test_lawyer_false_ratio(self, cursor):
        cursor.execute("SELECT count(*) FROM approvals WHERE lawyer = FALSE")
        actual = cursor.fetchone()[0] / N_CONTRACTS_TOTAL
        assert abs(actual - LAWYER_FALSE_RATIO) < TOLERANCE

    def test_protocol_no_lawyer_text_ratio(self, cursor):
        cursor.execute("""
            SELECT count(*) FROM protocols
            WHERE protocol_text ILIKE '%%разрешено согласование без юристов%%'
        """)
        actual = cursor.fetchone()[0] / N_PROTOCOLS
        assert abs(actual - PROTOCOL_NO_LAWYER_RATIO) < TOLERANCE

    def test_main_vs_ds_split(self, cursor):
        cursor.execute(
            "SELECT count(*) FROM contracts WHERE contract_number LIKE 'Договор %%'"
        )
        main_count = cursor.fetchone()[0]
        assert main_count == N_MAIN
        assert N_CONTRACTS_TOTAL - main_count == N_DS


# ---------------------------------------------------------------------------
# Scenario 1: Unapproved non-standard contracts
# ---------------------------------------------------------------------------

# Shared CTE that builds contract groups (used by audit queries and verification).
CONTRACT_GROUPS_CTE = """
WITH parsed_contracts AS (
    SELECT c.*,
           CASE
               WHEN c.contract_number LIKE 'Договор %%' THEN NULL
               ELSE substring(c.contract_number FROM '(\\d+)\\s*$')
           END AS parent_num
    FROM contracts c
),
contract_groups AS (
    SELECT pc.*,
           COALESCE(parent.contract_id, pc.contract_id) AS group_id
    FROM parsed_contracts pc
    LEFT JOIN contracts parent
      ON parent.contract_number = 'Договор ' || pc.parent_num
)
"""

AUDIT_UNAPPROVED_SQL_BASE = (
    CONTRACT_GROUPS_CTE
    + """,
group_totals AS (
    SELECT group_id, SUM(amount) AS total_amount
    FROM contract_groups
    GROUP BY group_id
    HAVING SUM(amount) > 10000
),
groups_with_nonstandard AS (
    SELECT DISTINCT group_id
    FROM contract_groups
    WHERE contract_type = 'нестандартный'
),
groups_without_lawyer AS (
    SELECT DISTINCT cg.group_id
    FROM contract_groups cg
    JOIN approvals a ON a.contract_id = cg.contract_id
    WHERE a.lawyer = FALSE
),
groups_with_protocol_permission AS (
    SELECT DISTINCT cg.group_id
    FROM contract_groups cg
    JOIN protocols p ON p.protocol_id = cg.protocol_id
    WHERE p.protocol_text ILIKE '%%разрешено согласование без юристов%%'
)
SELECT cg.contract_id, cg.contract_number, cg.amount,
       cg.supplier, cg.contract_type, cg.group_id,
       gt.total_amount AS group_total_amount
FROM contract_groups cg
JOIN group_totals gt ON gt.group_id = cg.group_id
JOIN groups_with_nonstandard gns ON gns.group_id = cg.group_id
JOIN groups_without_lawyer gwl ON gwl.group_id = cg.group_id
LEFT JOIN groups_with_protocol_permission gpp ON gpp.group_id = cg.group_id
WHERE gpp.group_id IS NULL
ORDER BY cg.group_id, cg.contract_id
"""
)

AUDIT_UNAPPROVED_SQL = AUDIT_UNAPPROVED_SQL_BASE + "\nLIMIT 200;"


class TestAuditUnapprovedNonstandard:
    """Scenario 1: contracts that are non-standard, unapproved by lawyer,
    and not excused by protocol."""

    @pytest.fixture(scope="class")
    def results(self, cursor):
        cursor.execute(AUDIT_UNAPPROVED_SQL)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return columns, rows

    @pytest.fixture(scope="class")
    def group_ids(self, results):
        _, rows = results
        return list({row[5] for row in rows})

    def test_result_columns(self, results):
        columns, _ = results
        expected = [
            "contract_id",
            "contract_number",
            "amount",
            "supplier",
            "contract_type",
            "group_id",
            "group_total_amount",
        ]
        assert columns == expected

    def test_results_not_empty(self, results):
        _, rows = results
        assert len(rows) > 0

    def test_all_groups_exceed_threshold(self, results):
        """Every group_id in results must have total > 10,000."""
        _, rows = results
        for row in rows:
            group_total = row[6]  # group_total_amount
            assert float(group_total) > 10000

    def test_each_group_has_nonstandard(self, group_ids, cursor):
        """Every flagged group must contain at least one нестандартный contract.

        Uses the full contract_groups CTE to check the entire group,
        not just the LIMIT'd result rows.
        """
        placeholders = ",".join(["%s"] * len(group_ids))
        cursor.execute(
            CONTRACT_GROUPS_CTE
            + f"""
            SELECT DISTINCT group_id FROM contract_groups
            WHERE contract_type = 'нестандартный'
            AND group_id IN ({placeholders})
            """,
            group_ids,
        )
        groups_with_ns = {row[0] for row in cursor.fetchall()}
        for gid in group_ids:
            assert gid in groups_with_ns, f"group {gid} has no нестандартный"

    def test_each_group_has_unapproved_lawyer(self, group_ids, cursor):
        """Every flagged group must have at least one contract with lawyer=FALSE.

        Uses the full contract_groups CTE to check the entire group.
        """
        placeholders = ",".join(["%s"] * len(group_ids))
        cursor.execute(
            CONTRACT_GROUPS_CTE
            + f"""
            SELECT DISTINCT cg.group_id FROM contract_groups cg
            JOIN approvals a ON a.contract_id = cg.contract_id
            WHERE a.lawyer = FALSE
            AND cg.group_id IN ({placeholders})
            """,
            group_ids,
        )
        groups_with_ul = {row[0] for row in cursor.fetchall()}
        for gid in group_ids:
            assert gid in groups_with_ul, f"group {gid} has no unapproved lawyer"

    def test_no_group_has_protocol_permission(self, group_ids, cursor):
        """No flagged group should have a protocol with lawyer-skip permission."""
        if not group_ids:
            return
        placeholders = ",".join(["%s"] * len(group_ids))
        cursor.execute(
            CONTRACT_GROUPS_CTE
            + f"""
            SELECT DISTINCT cg.group_id FROM contract_groups cg
            JOIN protocols p ON p.protocol_id = cg.protocol_id
            WHERE p.protocol_text ILIKE '%%разрешено согласование без юристов%%'
            AND cg.group_id IN ({placeholders})
            """,
            group_ids,
        )
        groups_with_perm = {row[0] for row in cursor.fetchall()}
        for gid in group_ids:
            assert gid not in groups_with_perm, (
                f"group {gid} has protocol permission but was flagged"
            )


# ---------------------------------------------------------------------------
# Scenario 2: Similar-subject contract pairs
# ---------------------------------------------------------------------------

SIMILAR_SUBJECTS_SQL_BASE = """
SELECT c1.contract_number AS contract_1,
       c2.contract_number AS contract_2,
       c1.supplier,
       c1.subject AS subject_1,
       c2.subject AS subject_2,
       similarity(c1.subject, c2.subject) AS sim_score,
       c1.amount + c2.amount AS combined_amount
FROM contracts c1
JOIN contracts c2
  ON c1.supplier = c2.supplier
  AND c1.contract_id < c2.contract_id
  AND similarity(c1.subject, c2.subject) > 0.3
ORDER BY c1.supplier, sim_score DESC
"""

SIMILAR_SUBJECTS_SQL = SIMILAR_SUBJECTS_SQL_BASE + "\nLIMIT 100;"


class TestSimilarSubjects:
    """Scenario 2: same-supplier contract pairs with similar subjects."""

    @pytest.fixture(scope="class")
    def results(self, cursor):
        cursor.execute(SIMILAR_SUBJECTS_SQL)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return columns, rows

    def test_result_columns(self, results):
        columns, _ = results
        expected = [
            "contract_1",
            "contract_2",
            "supplier",
            "subject_1",
            "subject_2",
            "sim_score",
            "combined_amount",
        ]
        assert columns == expected

    def test_results_not_empty(self, results):
        _, rows = results
        assert len(rows) > 0

    def test_similarity_above_threshold(self, results):
        _, rows = results
        for row in rows:
            assert float(row[5]) > 0.3

    def test_same_supplier_in_pair(self, results, cursor):
        """Both contracts in each pair must share the reported supplier."""
        _, rows = results
        for row in rows[:20]:
            cursor.execute(
                "SELECT supplier FROM contracts WHERE contract_number = %s",
                (row[0],),
            )
            s1 = cursor.fetchone()[0]
            cursor.execute(
                "SELECT supplier FROM contracts WHERE contract_number = %s",
                (row[1],),
            )
            s2 = cursor.fetchone()[0]
            assert s1 == s2 == row[2]


# ---------------------------------------------------------------------------
# Scenario 3: Supplier-subject summary
# ---------------------------------------------------------------------------

SUPPLIER_SUMMARY_SQL_BASE = """
WITH similar_pairs AS (
    SELECT c1.supplier,
           c1.subject,
           c1.contract_id, c1.contract_number, c1.amount
    FROM contracts c1
    WHERE EXISTS (
        SELECT 1 FROM contracts c2
        WHERE c2.supplier = c1.supplier
          AND c2.contract_id != c1.contract_id
          AND similarity(c1.subject, c2.subject) > 0.3
    )
)
SELECT supplier,
       subject,
       string_agg(contract_number, ', ' ORDER BY contract_number) AS contract_numbers,
       SUM(amount) AS total_amount,
       COUNT(*) AS contract_count
FROM similar_pairs
GROUP BY supplier, subject
ORDER BY total_amount DESC
"""

SUPPLIER_SUMMARY_SQL = SUPPLIER_SUMMARY_SQL_BASE + "\nLIMIT 100;"


class TestSupplierSubjectSummary:
    """Scenario 3: aggregated similar-subject grouping by supplier."""

    @pytest.fixture(scope="class")
    def results(self, cursor):
        cursor.execute(SUPPLIER_SUMMARY_SQL)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return columns, rows

    def test_result_columns(self, results):
        columns, _ = results
        expected = [
            "supplier",
            "subject",
            "contract_numbers",
            "total_amount",
            "contract_count",
        ]
        assert columns == expected

    def test_results_not_empty(self, results):
        _, rows = results
        assert len(rows) > 0

    def test_all_counts_greater_than_one(self, results):
        """Each group must have multiple contracts (that's what similar means)."""
        _, rows = results
        for row in rows:
            assert row[4] > 1, f"supplier={row[0]} subject={row[1]} has count=1"

    def test_total_amount_positive(self, results):
        _, rows = results
        for row in rows:
            assert float(row[3]) > 0
