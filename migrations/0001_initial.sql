CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_ref TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    account_number TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (account_type IN ('CUSTOMER', 'INTERNAL')),
    currency TEXT NOT NULL,
    overdraft_limit NUMERIC NOT NULL DEFAULT 0.00,
    is_internal INTEGER NOT NULL DEFAULT 0 CHECK (is_internal IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_accounts_customer_id ON accounts(customer_id);

CREATE TABLE IF NOT EXISTS ledger_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_ref TEXT NOT NULL UNIQUE,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER', 'REVERSAL')),
    description TEXT NOT NULL,
    currency TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    reversal_of_transaction_id INTEGER UNIQUE REFERENCES ledger_transactions(id)
);
CREATE INDEX IF NOT EXISTS ix_ledger_transactions_posted_at ON ledger_transactions(posted_at);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES ledger_transactions(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    direction TEXT NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
    amount NUMERIC NOT NULL CHECK (amount > 0),
    posted_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_account_posted_at ON ledger_entries(account_id, posted_at);
CREATE INDEX IF NOT EXISTS ix_ledger_entries_transaction_id ON ledger_entries(transaction_id);
