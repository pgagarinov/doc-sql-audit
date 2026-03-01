"""Shared pytest fixtures for database tests."""

from pathlib import Path

import psycopg2
import pytest
from claude_agent_sdk import ClaudeAgentOptions

CLAUDE_MD = (Path(__file__).parent.parent / "CLAUDE.md").read_text()

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "doc_sql_audit",
    "user": "postgres",
    "password": "postgres",
}


@pytest.fixture(scope="session")
def db_conn():
    """Session-scoped database connection."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def cursor(db_conn):
    """Session-scoped read-only cursor."""
    cur = db_conn.cursor()
    yield cur
    cur.close()


AGENT_SYSTEM_PROMPT = f"""\
You are a SQL analyst. You have access to a PostgreSQL database via the \
execute_sql MCP tool. The database schema is:

TABLE protocols (
  protocol_id SERIAL PRIMARY KEY,
  protocol_number VARCHAR NOT NULL,
  protocol_date DATE NOT NULL,
  approved_amount NUMERIC(12,2) NOT NULL,
  protocol_text TEXT NOT NULL
);

TABLE contracts (
  contract_id SERIAL PRIMARY KEY,
  contract_number VARCHAR NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  supplier VARCHAR NOT NULL,
  contract_type VARCHAR NOT NULL,  -- 'стандартный' or 'нестандартный'
  protocol_id INTEGER REFERENCES protocols(protocol_id),
  subject VARCHAR NOT NULL
);

TABLE approvals (
  approval_id SERIAL PRIMARY KEY,
  contract_id INTEGER REFERENCES contracts(contract_id),
  fin_director BOOLEAN NOT NULL,
  lawyer BOOLEAN NOT NULL,
  security BOOLEAN NOT NULL,
  procurement_head BOOLEAN NOT NULL
);

Extensions: pg_trgm (provides similarity() function).

IMPORTANT: Do NOT explore the schema. It is given above. Go straight to \
writing and executing the SQL query. Use execute_sql to run your query. \
Return exactly the columns requested in the prompt.

The following domain notes apply:

{CLAUDE_MD}\
"""


@pytest.fixture(scope="session")
def agent_options():
    """Session-scoped ClaudeAgentOptions for MCP agent tests."""
    return ClaudeAgentOptions(
        model="sonnet",
        system_prompt=AGENT_SYSTEM_PROMPT,
        mcp_servers={
            "doc-sql-audit": {
                "command": "./toolbox",
                "args": ["--prebuilt", "postgres", "--stdio"],
                "env": {
                    "POSTGRES_HOST": DB_CONFIG["host"],
                    "POSTGRES_PORT": str(DB_CONFIG["port"]),
                    "POSTGRES_USER": DB_CONFIG["user"],
                    "POSTGRES_PASSWORD": DB_CONFIG["password"],
                    "POSTGRES_DATABASE": DB_CONFIG["dbname"],
                },
            }
        },
        allowed_tools=["mcp__doc-sql-audit__*"],
        max_turns=5,
    )
