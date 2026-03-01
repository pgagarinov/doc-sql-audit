"""End-to-end tests: Claude generates SQL from natural language via MCP.

Each scenario sends a natural language prompt to Claude via the Agent SDK.
Claude calls the MCP execute_sql tool to query the database.  We extract
the generated SQL, run it via psycopg2, and compare columns + row count
against deterministic reference SQL.

Reference row counts are precomputed constants (data is generated
deterministically).  Reference columns come from LIMIT 0 queries (instant).
"""

import asyncio
import os
import re

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock, query

from test_audit_logic import (
    AUDIT_UNAPPROVED_SQL_BASE,
    SIMILAR_SUBJECTS_SQL_BASE,
    SUPPLIER_SUMMARY_SQL_BASE,
)

pytestmark = pytest.mark.agent

# Precomputed reference row counts (deterministic data generation).
# Regenerate with: pixi run python /tmp/bench_all.py
REF_ROW_COUNT_S1 = 21204
REF_ROW_COUNT_S2 = 1019115
REF_ROW_COUNT_S3 = 19801

# Reference column names (cheap to derive via LIMIT 0).
REF_COLUMNS_S1 = [
    "contract_id", "contract_number", "amount", "supplier",
    "contract_type", "group_id", "group_total_amount",
]
REF_COLUMNS_S2 = [
    "contract_1", "contract_2", "supplier", "subject_1",
    "subject_2", "sim_score", "combined_amount",
]
REF_COLUMNS_S3 = [
    "supplier", "subject", "contract_numbers",
    "total_amount", "contract_count",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_execute_sql(messages: list) -> list[str]:
    """Return all SQL strings the agent passed to execute_sql."""
    sqls = []
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        for block in msg.content:
            if isinstance(block, ToolUseBlock) and "execute_sql" in block.name:
                sql = block.input.get("sql", "")
                if sql:
                    sqls.append(sql)
    return sqls


def clean_sql(sql: str) -> str:
    """Remove trailing semicolons and LIMIT clause from SQL."""
    sql = sql.rstrip().rstrip(";").rstrip()
    sql = re.sub(r"\s+LIMIT\s+\d+\s*$", "", sql, flags=re.IGNORECASE)
    return sql.rstrip().rstrip(";").rstrip()


def find_best_matching_sql(sqls: list[str], ref_columns: list[str], cursor) -> str:
    """Pick the agent SQL whose columns best overlap with reference columns.

    The agent often runs exploratory queries (schema inspection, counts) in
    addition to the main result query.  We score each SQL by how many of its
    columns match the reference set, and return the best match.
    """
    ref_set = set(ref_columns)
    best_sql = sqls[-1]  # fallback
    best_score = -1

    for sql in sqls:
        try:
            # LIMIT 0 returns column metadata without executing the query
            test_sql = clean_sql(sql) + "\nLIMIT 0"
            cursor.execute(test_sql)
            cols = {desc[0] for desc in cursor.description}
            score = len(cols & ref_set)
            if score > best_score:
                best_score = score
                best_sql = sql
        except Exception:
            continue
    return best_sql


def run_agent_query(options, prompt: str) -> tuple[list, str | None]:
    """Run a synchronous wrapper around the async agent query.

    Returns (messages, result_text).
    """

    async def _run():
        # Unset CLAUDECODE to allow nested Claude Code sessions from tests
        env_backup = os.environ.pop("CLAUDECODE", None)
        try:
            messages = []
            result_text = None
            async for message in query(prompt=prompt, options=options):
                messages.append(message)
                if isinstance(message, ResultMessage) and message.result:
                    result_text = message.result
            return messages, result_text
        finally:
            if env_backup is not None:
                os.environ["CLAUDECODE"] = env_backup

    return asyncio.run(_run())


def strip_order_by(sql: str) -> str:
    """Strip trailing ORDER BY clause (top-level only, not inside subqueries)."""
    order_positions = [
        m.start() for m in re.finditer(r"ORDER\s+BY", sql, re.IGNORECASE)
    ]
    for pos in reversed(order_positions):
        depth = 0
        for ch in sql[:pos]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        if depth == 0:
            return sql[:pos].rstrip()
    return sql


def count_rows(cursor, sql: str, timeout_s: int = 120) -> int:
    """Return the row count of a query without fetching all rows."""
    cursor.execute(f"SET statement_timeout = '{timeout_s}s'")
    try:
        cursor.execute(f"SELECT count(*) FROM ({strip_order_by(sql)}) AS _t")
        return cursor.fetchone()[0]
    finally:
        cursor.execute("SET statement_timeout = '0'")  # reset


def get_columns(cursor, sql: str) -> list[str]:
    """Return column names from a query without fetching any rows."""
    cursor.execute(clean_sql(sql) + "\nLIMIT 0")
    return [desc[0] for desc in cursor.description]


# ---------------------------------------------------------------------------
# Connection smoke test
# ---------------------------------------------------------------------------


class TestAgentMCPConnection:
    def test_connects_to_mcp(self, agent_options):
        """Agent can list tables and sees the expected schema."""
        messages, result_text = run_agent_query(
            agent_options, "List all tables in the database."
        )
        assert result_text is not None
        lower = result_text.lower()
        assert "contracts" in lower
        assert "protocols" in lower
        assert "approvals" in lower


# ---------------------------------------------------------------------------
# Scenario 1: Unapproved non-standard contracts
# ---------------------------------------------------------------------------

SCENARIO_1_PROMPT = """\
Find contract groups (a main contract together with all its supplementary \
agreements) where ALL of the following conditions are true:
1. The group's total amount (sum of main contract + all its ДС) exceeds 10,000 RUB
2. At least one contract in the group has type 'нестандартный'
3. At least one contract in the group lacks lawyer approval (lawyer = FALSE in approvals)
4. The protocol associated with contracts in the group does NOT contain the phrase \
'разрешено согласование без юристов'

Use the ДС grouping approach described in the domain notes.

Return exactly these columns: contract_id, contract_number, amount, supplier, \
contract_type, group_id, group_total_amount.
Return ALL matching rows — do not use LIMIT. Order by group_id, contract_id.
Execute a single final SQL query that returns the full row-level detail. \
Do not summarize or aggregate into counts.\
"""


class TestAgentScenario1:
    @pytest.fixture(scope="class")
    def agent_result(self, agent_options):
        return run_agent_query(agent_options, SCENARIO_1_PROMPT)

    def test_agent_uses_execute_sql(self, agent_result):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        assert len(sqls) > 0, "Agent never called execute_sql"

    def test_columns_match(self, agent_result, cursor):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        agent_sql = find_best_matching_sql(sqls, REF_COLUMNS_S1, cursor)
        agent_columns = get_columns(cursor, agent_sql)
        assert sorted(agent_columns) == sorted(REF_COLUMNS_S1)

    def test_row_count_matches(self, agent_result, cursor):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        agent_sql = find_best_matching_sql(sqls, REF_COLUMNS_S1, cursor)
        agent_row_count = count_rows(cursor, clean_sql(agent_sql))
        assert agent_row_count == REF_ROW_COUNT_S1


# ---------------------------------------------------------------------------
# Scenario 2: Similar-subject contract pairs
# ---------------------------------------------------------------------------

SCENARIO_2_PROMPT = """\
Find pairs of contracts from the same supplier where the subjects have \
trigram similarity > 0.3. Use c1.contract_id < c2.contract_id to avoid \
duplicate pairs.

Return exactly these columns: contract_1, contract_2, supplier, subject_1, \
subject_2, sim_score, combined_amount.
Order by supplier, then by sim_score descending.
Use LIMIT 1000.
Execute a single final SQL query that returns the row-level detail. \
Do not summarize or aggregate into counts.\
"""


class TestAgentScenario2:
    @pytest.fixture(scope="class")
    def agent_result(self, agent_options):
        return run_agent_query(agent_options, SCENARIO_2_PROMPT)

    def test_agent_uses_execute_sql(self, agent_result):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        assert len(sqls) > 0, "Agent never called execute_sql"

    def test_columns_match(self, agent_result, cursor):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        agent_sql = find_best_matching_sql(sqls, REF_COLUMNS_S2, cursor)
        agent_columns = get_columns(cursor, agent_sql)
        assert sorted(agent_columns) == sorted(REF_COLUMNS_S2)

    def test_row_count_matches(self, agent_result, cursor):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        agent_sql = find_best_matching_sql(sqls, REF_COLUMNS_S2, cursor)
        agent_row_count = count_rows(cursor, clean_sql(agent_sql))
        assert agent_row_count == REF_ROW_COUNT_S2


# ---------------------------------------------------------------------------
# Scenario 3: Supplier-subject summary
# ---------------------------------------------------------------------------

SCENARIO_3_PROMPT = """\
For each supplier, group contracts that have similar subjects (trigram \
similarity > 0.3 to at least one other contract from that supplier). \
Group by supplier and subject.

Return exactly these columns: supplier, subject, contract_numbers \
(comma-separated via string_agg), total_amount, contract_count.
Order by total_amount descending.
Use LIMIT 1000.
Execute a single final SQL query that returns the row-level detail. \
Do not summarize or aggregate into counts.\
"""


class TestAgentScenario3:
    @pytest.fixture(scope="class")
    def agent_result(self, agent_options):
        return run_agent_query(agent_options, SCENARIO_3_PROMPT)

    def test_agent_uses_execute_sql(self, agent_result):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        assert len(sqls) > 0, "Agent never called execute_sql"

    def test_columns_match(self, agent_result, cursor):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        agent_sql = find_best_matching_sql(sqls, REF_COLUMNS_S3, cursor)
        agent_columns = get_columns(cursor, agent_sql)
        assert sorted(agent_columns) == sorted(REF_COLUMNS_S3)

    def test_row_count_matches(self, agent_result, cursor):
        messages, _ = agent_result
        sqls = extract_execute_sql(messages)
        agent_sql = find_best_matching_sql(sqls, REF_COLUMNS_S3, cursor)
        agent_row_count = count_rows(cursor, clean_sql(agent_sql))
        assert agent_row_count == REF_ROW_COUNT_S3
