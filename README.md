# Core Banking Ledger Mini-System

A production-style backend for a core banking mini-system built with Python's standard library and SQLite. It manages customers and accounts, posts money movement through an immutable double-entry ledger, enforces overdraft limits, supports idempotent posting via unique `external_ref`, and exposes HTTP APIs for balances and statements.

## Features

- Create customers and accounts.
- Post deposit, withdrawal, transfer, and reversal operations.
- Store every operation as an immutable `ledger_transactions` row plus two or more append-only `ledger_entries` rows.
- Enforce balanced accounting with `sum(debits) == sum(credits)` for every posted transaction.
- Use an internal `SETTLEMENT_CASH` account to balance deposits and withdrawals.
- Prevent duplicate posting with unique idempotency key `external_ref`.
- Compute balances in real time with the convention `credits - debits`.
- Generate account statements by date range with opening, running, and closing balances.
- Block overdrafts unless the account's `overdraft_limit` permits the posting.
- Apply all writes atomically inside database transactions.
- Include migration SQL, initialization scripts, and unit tests.

## Accounting model

### Balance convention

The system uses this balance formula for every account:

```text
balance = total_credits - total_debits
```

That means:

- Depositing into a customer account creates a **credit** to the customer account, increasing the displayed balance.
- Withdrawing from a customer account creates a **debit** to the customer account, reducing the displayed balance.
- A transfer debits the source account and credits the destination account.

### Why `SETTLEMENT_CASH` exists

External cash movements still need a balanced journal entry.

- **Deposit** = debit `SETTLEMENT_CASH`, credit customer account.
- **Withdrawal** = debit customer account, credit `SETTLEMENT_CASH`.

This keeps every transaction balanced without inventing or destroying money inside the ledger.

### Immutability and reversal

Ledger rows are append-only.

- The system never edits prior ledger entries.
- A reversal creates a brand-new `REVERSAL` transaction with mirrored directions.
- The original transaction remains intact for auditability.

## Schema and constraints

The SQL migration creates:

- `customers`
- `accounts`
- `ledger_transactions`
- `ledger_entries`

Key protections included:

- unique `customers.external_ref`
- unique `accounts.account_number`
- unique `ledger_transactions.external_ref`
- unique `ledger_transactions.reversal_of_transaction_id` to prevent double reversal
- foreign keys between transactions, entries, customers, and accounts
- indexes for account statement queries and transaction lookup

See `migrations/0001_initial.sql` for the exact schema.

## Project structure

```text
app/
  api.py
  config.py
  db.py
  main.py
  models.py
  service.py
migrations/
  0001_initial.sql
scripts/
  migrate.py
  init_internal_account.py
tests/
  test_banking.py
```

## Setup

### 1. Optional virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install the package

```bash
pip install -e '.[dev]'
```

No third-party runtime dependencies are required.

### 3. Configure the database

By default the app writes to `banking.db` in the project root.

Optional environment variable:

```bash
export DATABASE_URL=./banking.db
```

### 4. Apply migration + bootstrap the internal account

```bash
python scripts/migrate.py
```

This applies `migrations/0001_initial.sql` and ensures the internal `SETTLEMENT_CASH` account exists.

### 5. Start the HTTP server

```bash
python -m app.main
```

The API will listen on `http://127.0.0.1:8000`.

## HTTP API examples

### Create a customer

```bash
curl -X POST http://127.0.0.1:8000/customers \
  -H 'Content-Type: application/json' \
  -d '{
    "external_ref": "cust-001",
    "name": "Alice Doe"
  }'
```

### Create an account

```bash
curl -X POST http://127.0.0.1:8000/accounts \
  -H 'Content-Type: application/json' \
  -d '{
    "customer_id": 2,
    "account_number": "CHK-0001",
    "name": "Alice Checking",
    "currency": "USD",
    "overdraft_limit": "100.00"
  }'
```

### Deposit

```bash
curl -X POST http://127.0.0.1:8000/ledger/deposit \
  -H 'Content-Type: application/json' \
  -d '{
    "external_ref": "dep-001",
    "account_id": 2,
    "amount": "250.00",
    "currency": "USD",
    "description": "Initial funding"
  }'
```

### Withdraw

```bash
curl -X POST http://127.0.0.1:8000/ledger/withdraw \
  -H 'Content-Type: application/json' \
  -d '{
    "external_ref": "wd-001",
    "account_id": 2,
    "amount": "25.00",
    "currency": "USD",
    "description": "ATM cash"
  }'
```

### Transfer

```bash
curl -X POST http://127.0.0.1:8000/ledger/transfer \
  -H 'Content-Type: application/json' \
  -d '{
    "external_ref": "trf-001",
    "source_account_id": 2,
    "destination_account_id": 3,
    "amount": "40.00",
    "currency": "USD",
    "description": "Rent split"
  }'
```

### Reverse a transaction

```bash
curl -X POST http://127.0.0.1:8000/ledger/reverse \
  -H 'Content-Type: application/json' \
  -d '{
    "external_ref": "rev-001",
    "transaction_id": 10,
    "description": "Operator reversal"
  }'
```

### Fetch real-time balance

```bash
curl http://127.0.0.1:8000/accounts/2/balance
```

Example response shape:

```json
{
  "account_id": 2,
  "account_number": "CHK-0001",
  "currency": "USD",
  "balance": "225.00",
  "convention": "credits_minus_debits",
  "overdraft_limit": "100.00",
  "available_funds": "325.00"
}
```

### Fetch statement by date range

```bash
curl 'http://127.0.0.1:8000/accounts/2/statement?start_date=2026-01-01&end_date=2026-12-31'
```

## Tests

Run:

```bash
pytest
```

The automated tests cover:

- balanced double-entry posting
- idempotency / replay safety
- insufficient funds enforcement
- transfer correctness
- reversal correctness
- statement generation
