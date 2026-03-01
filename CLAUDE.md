# doc-sql-audit

Procurement contract audit system. Uses genai-toolbox `--prebuilt postgres` MCP server
to explore the database schema dynamically and execute SQL queries.

## MCP Tool Usage

The `doc-sql-audit` MCP server provides built-in PostgreSQL tools:
- `list_tables` — discover tables, columns, constraints, indexes
- `execute_sql` — run any SQL query
- `list_indexes`, `list_schemas`, etc. — explore database structure

Always start by exploring the schema with `list_tables` before writing queries.

## Important Domain Notes

- **Supplementary agreements (ДС)** reference parent contracts only via text
  patterns in `contract_number`. There is NO foreign key column.
  Main contracts follow the pattern `Договор N`.
  All ДС formats end with the parent contract number as the last integer.
  To group ДС with their parent, extract the trailing number:
  `substring(contract_number FROM '(\d+)\s*$')`
  Then join: `'Договор ' || extracted_number`.
  Do NOT use complex regex to match specific ДС name formats.
- **pg_trgm** extension is enabled for fuzzy text matching via `similarity()`.
- Database locale is `ru_RU.UTF-8` (Cyrillic trigrams work correctly).
- Always use `LIMIT` to avoid returning excessive rows.

## Audit Scenarios

### Scenario 1: Unapproved Non-standard Contracts

Find contract groups (a main contract together with all its supplementary agreements)
where ALL of the following conditions are true:
1. The group's total amount (sum of main contract + all its ДС) exceeds 10,000 RUB
2. At least one contract in the group has type 'нестандартный'
3. At least one contract in the group lacks lawyer approval (lawyer = FALSE in approvals)
4. The protocol associated with contracts in the group does NOT contain the phrase
   'разрешено согласование без юристов'

### Scenario 2: Similar-Subject Contract Pairs (Splitting Detection)

Find pairs of contracts from the same supplier where the subjects have trigram
similarity > 0.3. Return both contract numbers, the supplier, both subjects,
the similarity score, and the combined amount. Order by supplier then by
similarity score descending. Use LIMIT.

### Scenario 3: Supplier-Subject Summary (Aggregated Splitting View)

For each supplier, group contracts that have similar subjects (trigram similarity > 0.3
to at least one other contract from that supplier). Show the supplier, subject,
comma-separated list of contract numbers, total amount, and contract count.
Order by total amount descending. Use LIMIT.

## Data Generation

```bash
pixi run python -m doc_sql_audit.generate_data
```

Generation parameters are in `src/doc_sql_audit/constants.py`.

## Running Tests

```bash
pixi run pytest tests/ -v
```
