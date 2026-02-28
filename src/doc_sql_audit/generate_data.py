"""Generate synthetic Russian procurement data and insert into PostgreSQL."""

import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from faker import Faker

from doc_sql_audit.constants import (
    N_MAIN,
    N_DS,
    N_PROTOCOLS,
    N_SUPPLIERS,
    NONSTANDARD_RATIO,
    PROTOCOL_NO_LAWYER_RATIO,
    LAWYER_FALSE_RATIO,
    DS_TEMPLATES,
    PROTOCOL_NO_LAWYER_TEXT,
    PROTOCOL_NORMAL_TEMPLATES,
    CONTRACT_SUBJECTS,
)

fake = Faker("ru_RU")
rng = np.random.default_rng(42)

DB_PARAMS = {
    "host": "127.0.0.1",
    "port": 5432,
    "dbname": "doc_sql_audit",
    "user": "postgres",
    "password": "postgres",
}

DDL = """
DROP TABLE IF EXISTS approvals CASCADE;
DROP TABLE IF EXISTS contracts CASCADE;
DROP TABLE IF EXISTS protocols CASCADE;

CREATE TABLE protocols (
    protocol_id SERIAL PRIMARY KEY,
    protocol_number VARCHAR NOT NULL,
    protocol_date DATE NOT NULL,
    approved_amount NUMERIC(12,2) NOT NULL,
    protocol_text TEXT NOT NULL
);

CREATE TABLE contracts (
    contract_id SERIAL PRIMARY KEY,
    contract_number VARCHAR NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    supplier VARCHAR NOT NULL,
    contract_type VARCHAR NOT NULL,
    protocol_id INTEGER REFERENCES protocols(protocol_id),
    subject VARCHAR NOT NULL
);

CREATE TABLE approvals (
    approval_id SERIAL PRIMARY KEY,
    contract_id INTEGER REFERENCES contracts(contract_id),
    fin_director BOOLEAN NOT NULL,
    lawyer BOOLEAN NOT NULL,
    security BOOLEAN NOT NULL,
    procurement_head BOOLEAN NOT NULL
);

CREATE INDEX idx_contracts_supplier ON contracts(supplier);
CREATE INDEX idx_contracts_number ON contracts(contract_number);
CREATE INDEX idx_contracts_subject_trgm ON contracts USING gin (subject gin_trgm_ops);
"""


def generate_suppliers(n: int) -> list[str]:
    """Generate a pool of unique supplier company names."""
    suppliers = set()
    while len(suppliers) < n:
        suppliers.add(fake.company())
    return list(suppliers)


def generate_protocols(n: int) -> list[tuple]:
    """Generate protocol records."""
    rows = []
    no_lawyer_count = int(n * PROTOCOL_NO_LAWYER_RATIO)
    no_lawyer_flags = np.zeros(n, dtype=bool)
    no_lawyer_flags[:no_lawyer_count] = True
    rng.shuffle(no_lawyer_flags)

    for i in range(n):
        protocol_number = f"ПР-{i + 1:05d}"
        protocol_date = fake.date_between(start_date="-3y", end_date="today")
        approved_amount = float(rng.integers(5_000, 5_000_000))

        members = ", ".join(fake.name() for _ in range(3))
        template = rng.choice(PROTOCOL_NORMAL_TEMPLATES)
        text = template.format(amount=approved_amount, members=members)
        if no_lawyer_flags[i]:
            text += f" {PROTOCOL_NO_LAWYER_TEXT}."

        rows.append((protocol_number, protocol_date, approved_amount, text))

    return rows


def generate_main_contracts(
    n: int, suppliers: list[str], n_protocols: int
) -> list[tuple]:
    """Generate main contract records (contract_number = 'Договор N')."""
    rows = []
    for i in range(n):
        contract_number = f"Договор {i + 1}"
        amount = float(rng.integers(100, 500_000))
        supplier = suppliers[rng.integers(0, len(suppliers))]
        contract_type = (
            "нестандартный" if rng.random() < NONSTANDARD_RATIO else "стандартный"
        )
        protocol_id = int(rng.integers(1, n_protocols + 1))
        subject = rng.choice(CONTRACT_SUBJECTS)
        rows.append(
            (contract_number, amount, supplier, contract_type, protocol_id, subject)
        )
    return rows


def generate_ds_contracts(
    n: int, n_main: int, main_contracts: list[tuple]
) -> list[tuple]:
    """Generate supplementary agreement records.

    Each ДС references a random parent main contract and inherits its supplier.
    """
    rows = []
    for _ in range(n):
        parent_idx = int(rng.integers(0, n_main))
        parent_number = parent_idx + 1  # "Договор {parent_number}"
        parent_row = main_contracts[parent_idx]
        supplier = parent_row[2]  # inherit supplier from parent
        protocol_id = parent_row[4]  # inherit protocol from parent

        template = rng.choice(DS_TEMPLATES)
        contract_number = template.format(parent_number=parent_number)
        amount = float(rng.integers(100, 200_000))
        contract_type = (
            "нестандартный" if rng.random() < NONSTANDARD_RATIO else "стандартный"
        )
        subject = rng.choice(CONTRACT_SUBJECTS)
        rows.append(
            (contract_number, amount, supplier, contract_type, protocol_id, subject)
        )
    return rows


def generate_approvals(n_total: int) -> list[tuple]:
    """Generate approval records, one per contract.

    contract_id values are 1..n_total (matching SERIAL order).
    """
    rows = []
    lawyer_false_count = int(n_total * LAWYER_FALSE_RATIO)
    lawyer_flags = np.ones(n_total, dtype=bool)
    lawyer_flags[:lawyer_false_count] = False
    rng.shuffle(lawyer_flags)

    for i in range(n_total):
        contract_id = i + 1
        fin_director = bool(rng.random() < 0.95)
        lawyer = bool(lawyer_flags[i])
        security = bool(rng.random() < 0.90)
        procurement_head = bool(rng.random() < 0.95)
        rows.append((contract_id, fin_director, lawyer, security, procurement_head))
    return rows


def main():
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True
    cur = conn.cursor()

    print("Creating tables...")
    cur.execute(DDL)

    print(f"Generating {N_PROTOCOLS} protocols...")
    protocols = generate_protocols(N_PROTOCOLS)
    execute_values(
        cur,
        "INSERT INTO protocols (protocol_number, protocol_date, approved_amount, protocol_text) "
        "VALUES %s",
        protocols,
    )
    print(f"  Inserted {len(protocols)} protocols.")

    suppliers = generate_suppliers(N_SUPPLIERS)
    print(f"Generated {len(suppliers)} unique suppliers.")

    print(f"Generating {N_MAIN} main contracts...")
    main_contracts = generate_main_contracts(N_MAIN, suppliers, N_PROTOCOLS)

    print(f"Generating {N_DS} supplementary agreements...")
    ds_contracts = generate_ds_contracts(N_DS, N_MAIN, main_contracts)

    all_contracts = main_contracts + ds_contracts
    print(f"Inserting {len(all_contracts)} contracts...")
    execute_values(
        cur,
        "INSERT INTO contracts (contract_number, amount, supplier, contract_type, protocol_id, subject) "
        "VALUES %s",
        all_contracts,
    )
    print(f"  Inserted {len(all_contracts)} contracts.")

    n_total = len(all_contracts)
    print(f"Generating {n_total} approvals...")
    approvals = generate_approvals(n_total)
    execute_values(
        cur,
        "INSERT INTO approvals (contract_id, fin_director, lawyer, security, procurement_head) "
        "VALUES %s",
        approvals,
    )
    print(f"  Inserted {len(approvals)} approvals.")

    # Verify
    cur.execute("SELECT count(*) FROM contracts")
    print(f"\nVerification: {cur.fetchone()[0]} contracts in database.")
    cur.execute("SELECT count(*) FROM protocols")
    print(f"Verification: {cur.fetchone()[0]} protocols in database.")
    cur.execute("SELECT count(*) FROM approvals")
    print(f"Verification: {cur.fetchone()[0]} approvals in database.")

    cur.close()
    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
