# doc-sql-audit

Procurement contract audit system with a synthetic Russian-language database
(~100k contracts, supplementary agreements, protocols, approvals).
Designed as a testbed for AI-driven SQL auditing via Claude Code + MCP.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- [pixi](https://pixi.sh) — Python environment & task runner
- PostgreSQL running locally (user `postgres`, database `doc_sql_audit`)

### MCP servers

The project uses two MCP servers that Claude Code picks up automatically.

**1. genai-toolbox (Postgres)** — configured in `.mcp.json`, provides
`list_tables`, `execute_sql`, `list_indexes`, etc.

```bash
brew install mcp-toolbox
```

**2. Context7** — fetches up-to-date library documentation into prompts.

```bash
claude mcp add context7 -- npx -y @upstash/context7-mcp@latest
```

Requires Node.js >= 18.

### Claude Code skills

**ast-index** — fast code search and symbol navigation.

```bash
brew tap defendend/ast-index
brew install ast-index
ast-index install-claude-plugin
ast-index rebuild   # index the project
```

## Quick start

```bash
# generate the synthetic database
pixi run python -m doc_sql_audit.generate_data

# run tests
pixi run test        # unit tests only
pixi run test-agent  # agent tests (requires Claude Max subscription)
pixi run test-all    # everything
```

## Database schema

```
CONTRACTS  ──┐          PROTOCOLS
  contract_id PK        │  protocol_id PK
  contract_number       │  protocol_number
  amount                │  protocol_date
  supplier              │  approved_amount
  contract_type         │  protocol_text
  protocol_id FK  ──────┘
  subject

APPROVALS
  approval_id PK
  contract_id FK  ──────── CONTRACTS
  fin_director bool
  lawyer bool
  security bool
  procurement_head bool
```

Supplementary agreements (ДС) are linked to parent contracts only by a naming
convention in `contract_number` — there is no foreign key. To group them,
extract the trailing number: `substring(contract_number FROM '(\d+)\s*$')`.

## Audit scenarios

1. **Unapproved non-standard contracts** — contract groups where the total
   exceeds 10 000 RUB, at least one is non-standard, lacks lawyer approval,
   and the protocol does not permit bypassing legal review.
2. **Similar-subject contract pairs** — pairs from the same supplier with
   trigram similarity > 0.3 (contract splitting detection).
3. **Supplier-subject summary** — aggregated view grouping similar contracts
   per supplier with totals.

## Project structure

```
src/doc_sql_audit/
  constants.py      — generation parameters & vocabularies
  generate_data.py  — synthetic data generator
tests/
  test_schema.py       — DB schema validation
  test_audit_logic.py  — audit query correctness
  test_agent_mcp.py    — end-to-end agent tests via Claude Agent SDK
```

## MCP configuration

The `.mcp.json` at the repo root configures the `genai-toolbox` Postgres MCP
server. Claude Code picks it up automatically and gains `list_tables`,
`execute_sql`, `list_indexes`, etc.
