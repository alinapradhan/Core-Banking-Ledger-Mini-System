from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable

from app.config import settings
from app.db import get_connection, transaction
from app.models import Account, Customer, LedgerEntry, LedgerTransaction, decimalize

ZERO = Decimal("0.00")
BALANCE_CONVENTION = "credits_minus_debits"


class BankingError(Exception):
    pass


class ConflictError(BankingError):
    pass


class NotFoundError(BankingError):
    pass


class InsufficientFundsError(BankingError):
    pass


class ValidationError(BankingError):
    pass


class BankingService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_customer(self, external_ref: str, name: str) -> Customer:
        try:
            with transaction(self.connection):
                cursor = self.connection.execute(
                    "INSERT INTO customers (external_ref, name) VALUES (?, ?)",
                    (external_ref, name),
                )
            return self.get_customer(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Customer external_ref already exists") from exc

    def get_customer(self, customer_id: int) -> Customer:
        row = self.connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise NotFoundError("Customer not found")
        return Customer(**dict(row))

    def create_account(
        self,
        *,
        customer_id: int | None,
        account_number: str,
        name: str,
        currency: str = "USD",
        overdraft_limit: Decimal = ZERO,
        is_internal: bool = False,
    ) -> Account:
        currency = currency.upper()
        if customer_id is not None:
            self.get_customer(customer_id)
        account_type = "INTERNAL" if is_internal else "CUSTOMER"
        try:
            with transaction(self.connection):
                cursor = self.connection.execute(
                    """
                    INSERT INTO accounts (
                        customer_id, account_number, name, account_type, currency, overdraft_limit, is_internal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (customer_id, account_number, name, account_type, currency, str(decimalize(overdraft_limit)), int(is_internal)),
                )
            return self.get_account(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("Account account_number must be unique") from exc

    def get_account(self, account_id: int) -> Account:
        row = self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise NotFoundError("Account not found")
        data = dict(row)
        data["overdraft_limit"] = decimalize(data["overdraft_limit"])
        data["is_internal"] = bool(data["is_internal"])
        return Account(**data)

    def ensure_internal_settlement_account(self) -> Account:
        existing = self.connection.execute(
            "SELECT id FROM accounts WHERE account_number = ?",
            (settings.settlement_cash_account_number,),
        ).fetchone()
        if existing:
            return self.get_account(existing["id"])

        with transaction(self.connection):
            customer = self.connection.execute(
                "SELECT id FROM customers WHERE external_ref = ?",
                (settings.settlement_cash_customer_name,),
            ).fetchone()
            if customer is None:
                customer_id = self.connection.execute(
                    "INSERT INTO customers (external_ref, name) VALUES (?, ?)",
                    (settings.settlement_cash_customer_name, settings.settlement_cash_customer_name),
                ).lastrowid
            else:
                customer_id = customer["id"]
            account_id = self.connection.execute(
                """
                INSERT INTO accounts (
                    customer_id, account_number, name, account_type, currency, overdraft_limit, is_internal
                ) VALUES (?, ?, ?, 'INTERNAL', 'USD', ?, 1)
                """,
                (customer_id, settings.settlement_cash_account_number, settings.settlement_cash_account_name, "999999999999.99"),
            ).lastrowid
        return self.get_account(account_id)

    def post_deposit(self, *, account_id: int, amount: Decimal, currency: str, external_ref: str, description: str) -> LedgerTransaction:
        settlement = self.ensure_internal_settlement_account()
        account = self._validated_account(account_id, currency)
        return self._post_transaction(
            external_ref=external_ref,
            transaction_type="DEPOSIT",
            description=description,
            currency=currency,
            entries=[
                {"account_id": settlement.id, "direction": "DEBIT", "amount": decimalize(amount)},
                {"account_id": account.id, "direction": "CREDIT", "amount": decimalize(amount)},
            ],
        )

    def post_withdrawal(self, *, account_id: int, amount: Decimal, currency: str, external_ref: str, description: str) -> LedgerTransaction:
        account = self._validated_account(account_id, currency)
        amount = decimalize(amount)
        self._ensure_sufficient_funds(account, amount)
        settlement = self.ensure_internal_settlement_account()
        return self._post_transaction(
            external_ref=external_ref,
            transaction_type="WITHDRAWAL",
            description=description,
            currency=currency,
            entries=[
                {"account_id": account.id, "direction": "DEBIT", "amount": amount},
                {"account_id": settlement.id, "direction": "CREDIT", "amount": amount},
            ],
        )

    def post_transfer(
        self,
        *,
        source_account_id: int,
        destination_account_id: int,
        amount: Decimal,
        currency: str,
        external_ref: str,
        description: str,
    ) -> LedgerTransaction:
        if source_account_id == destination_account_id:
            raise ValidationError("Source and destination accounts must differ")
        source = self._validated_account(source_account_id, currency)
        destination = self._validated_account(destination_account_id, currency)
        amount = decimalize(amount)
        self._ensure_sufficient_funds(source, amount)
        return self._post_transaction(
            external_ref=external_ref,
            transaction_type="TRANSFER",
            description=description,
            currency=currency,
            entries=[
                {"account_id": source.id, "direction": "DEBIT", "amount": amount},
                {"account_id": destination.id, "direction": "CREDIT", "amount": amount},
            ],
        )

    def post_reversal(self, *, transaction_id: int, external_ref: str, description: str) -> LedgerTransaction:
        existing = self.find_transaction_by_external_ref(external_ref)
        if existing is not None:
            return existing
        original = self.get_transaction(transaction_id)
        if original.transaction_type == "REVERSAL":
            raise ValidationError("Reversal transactions cannot be reversed")
        reversed_entries: list[dict[str, object]] = []
        for entry in original.entries:
            direction = "CREDIT" if entry.direction == "DEBIT" else "DEBIT"
            account = self.get_account(entry.account_id)
            if direction == "DEBIT":
                self._ensure_sufficient_funds(account, entry.amount)
            reversed_entries.append({"account_id": entry.account_id, "direction": direction, "amount": entry.amount})
        return self._post_transaction(
            external_ref=external_ref,
            transaction_type="REVERSAL",
            description=description,
            currency=original.currency,
            reversal_of_transaction_id=original.id,
            entries=reversed_entries,
        )

    def get_balance(self, account_id: int) -> Decimal:
        self.get_account(account_id)
        return self._balance_for_account(account_id)

    def get_statement(self, account_id: int, start_date: date, end_date: date) -> dict:
        account = self.get_account(account_id)
        if end_date < start_date:
            raise ValidationError("end_date must be on or after start_date")
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
        opening_balance = self._balance_for_account(account_id, posted_before=start_dt.isoformat())
        rows = self.connection.execute(
            """
            SELECT le.*, lt.external_ref, lt.transaction_type, lt.description
            FROM ledger_entries le
            JOIN ledger_transactions lt ON lt.id = le.transaction_id
            WHERE le.account_id = ? AND le.posted_at >= ? AND le.posted_at <= ?
            ORDER BY le.posted_at, le.id
            """,
            (account_id, start_dt.isoformat(), end_dt.isoformat()),
        ).fetchall()
        running_balance = opening_balance
        entries = []
        for row in rows:
            amount = decimalize(row["amount"])
            running_balance += amount if row["direction"] == "CREDIT" else -amount
            entries.append(
                {
                    "transaction_id": row["transaction_id"],
                    "external_ref": row["external_ref"],
                    "transaction_type": row["transaction_type"],
                    "posted_at": row["posted_at"],
                    "description": row["description"],
                    "direction": row["direction"],
                    "amount": f"{amount:.2f}",
                    "running_balance": f"{running_balance:.2f}",
                }
            )
        return {
            "account_id": account.id,
            "account_number": account.account_number,
            "currency": account.currency,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "opening_balance": f"{opening_balance:.2f}",
            "closing_balance": f"{running_balance:.2f}",
            "convention": BALANCE_CONVENTION,
            "entries": entries,
        }

    def get_transaction(self, transaction_id: int) -> LedgerTransaction:
        row = self.connection.execute("SELECT * FROM ledger_transactions WHERE id = ?", (transaction_id,)).fetchone()
        if row is None:
            raise NotFoundError("Transaction not found")
        entries_rows = self.connection.execute(
            "SELECT * FROM ledger_entries WHERE transaction_id = ? ORDER BY id",
            (transaction_id,),
        ).fetchall()
        entries = [
            LedgerEntry(
                id=item["id"],
                transaction_id=item["transaction_id"],
                account_id=item["account_id"],
                direction=item["direction"],
                amount=decimalize(item["amount"]),
                posted_at=item["posted_at"],
                created_at=item["created_at"],
            )
            for item in entries_rows
        ]
        return LedgerTransaction(entries=entries, **dict(row))

    def find_transaction_by_external_ref(self, external_ref: str) -> LedgerTransaction | None:
        row = self.connection.execute(
            "SELECT id FROM ledger_transactions WHERE external_ref = ?",
            (external_ref,),
        ).fetchone()
        return None if row is None else self.get_transaction(row["id"])

    def _post_transaction(
        self,
        *,
        external_ref: str,
        transaction_type: str,
        description: str,
        currency: str,
        entries: list[dict[str, object]],
        reversal_of_transaction_id: int | None = None,
    ) -> LedgerTransaction:
        existing = self.find_transaction_by_external_ref(external_ref)
        if existing is not None:
            return existing
        self._validate_balanced(entries)
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with transaction(self.connection):
                transaction_id = self.connection.execute(
                    """
                    INSERT INTO ledger_transactions (
                        external_ref, transaction_type, description, currency, posted_at, reversal_of_transaction_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (external_ref, transaction_type, description, currency.upper(), timestamp, reversal_of_transaction_id),
                ).lastrowid
                for entry in entries:
                    self.connection.execute(
                        """
                        INSERT INTO ledger_entries (
                            transaction_id, account_id, direction, amount, posted_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transaction_id,
                            entry["account_id"],
                            entry["direction"],
                            f"{decimalize(entry['amount']):.2f}",
                            timestamp,
                            timestamp,
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            existing = self.find_transaction_by_external_ref(external_ref)
            if existing is not None:
                return existing
            raise ConflictError("Ledger transaction conflict") from exc
        return self.get_transaction(transaction_id)

    def _validate_balanced(self, entries: Iterable[dict[str, object]]) -> None:
        entries = list(entries)
        if len(entries) < 2:
            raise ValidationError("Transactions must contain at least two entries")
        debits = sum(decimalize(item["amount"]) for item in entries if item["direction"] == "DEBIT")
        credits = sum(decimalize(item["amount"]) for item in entries if item["direction"] == "CREDIT")
        if debits != credits:
            raise ValidationError("Debits and credits must balance")

    def _validated_account(self, account_id: int, currency: str) -> Account:
        account = self.get_account(account_id)
        if account.currency != currency.upper():
            raise ValidationError("Currency mismatch")
        return account

    def _ensure_sufficient_funds(self, account: Account, amount: Decimal) -> None:
        projected = self._balance_for_account(account.id) - decimalize(amount)
        if projected < (ZERO - account.overdraft_limit):
            raise InsufficientFundsError("Insufficient funds")

    def _balance_for_account(self, account_id: int, posted_before: str | None = None) -> Decimal:
        query = """
            SELECT COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount ELSE 0 END), 0) -
                   COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount ELSE 0 END), 0) AS balance
            FROM ledger_entries
            WHERE account_id = ?
        """
        params: list[object] = [account_id]
        if posted_before is not None:
            query += " AND posted_at < ?"
            params.append(posted_before)
        row = self.connection.execute(query, params).fetchone()
        return decimalize(row["balance"] if row else "0.00")


def bootstrap_database(database_url: str | None = None) -> None:
    connection = get_connection(database_url)
    try:
        schema = open("migrations/0001_initial.sql", "r", encoding="utf-8").read()
        connection.executescript(schema)
        BankingService(connection).ensure_internal_settlement_account()
    finally:
        connection.close()
