from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import pytest

from app.api import JsonApi
from app.db import get_connection
from app.service import BankingService, bootstrap_database


@pytest.fixture()
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    bootstrap_database(str(db_path))
    connection = get_connection(str(db_path))
    try:
        yield str(db_path), connection
    finally:
        connection.close()


def request(app, method: str, path: str, body: dict | None = None, query_string: str = ""):
    raw = json.dumps(body or {}).encode("utf-8")
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(raw)) if method == "POST" else "0",
        "wsgi.input": io.BytesIO(raw),
    }
    payload = b"".join(app(environ, start_response))
    return captured["status"], json.loads(payload.decode("utf-8"))


def create_customer_and_account(app):
    _, customer = request(app, "POST", "/customers", {"external_ref": "cust-1", "name": "Alice"})
    _, account = request(
        app,
        "POST",
        "/accounts",
        {
            "customer_id": customer["id"],
            "account_number": "CHK-1",
            "name": "Alice Checking",
            "currency": "USD",
            "overdraft_limit": "20.00",
        },
    )
    return customer, account


def test_balanced_entries_for_deposit(setup_db):
    db_path, connection = setup_db
    app = JsonApi(db_path)
    _, account = create_customer_and_account(app)

    status, body = request(
        app,
        "POST",
        "/ledger/deposit",
        {
            "external_ref": "dep-1",
            "account_id": account["id"],
            "amount": "150.00",
            "currency": "USD",
            "description": "Cash deposit",
        },
    )
    assert status == 201
    assert len(body["entries"]) == 2

    service = BankingService(connection)
    txn = service.find_transaction_by_external_ref("dep-1")
    debits = sum(entry.amount for entry in txn.entries if entry.direction == "DEBIT")
    credits = sum(entry.amount for entry in txn.entries if entry.direction == "CREDIT")
    assert debits == credits == Decimal("150.00")


def test_idempotency_prevents_double_post(setup_db):
    db_path, _ = setup_db
    app = JsonApi(db_path)
    _, account = create_customer_and_account(app)

    payload = {
        "external_ref": "dep-2",
        "account_id": account["id"],
        "amount": "25.00",
        "currency": "USD",
        "description": "Idempotent deposit",
    }
    first_status, first = request(app, "POST", "/ledger/deposit", payload)
    second_status, second = request(app, "POST", "/ledger/deposit", payload)

    assert first_status == 201
    assert second_status == 201
    assert first["id"] == second["id"]

    balance_status, balance = request(app, "GET", f"/accounts/{account['id']}/balance")
    assert balance_status == 200
    assert balance["balance"] == "25.00"


def test_insufficient_funds_is_rejected(setup_db):
    db_path, _ = setup_db
    app = JsonApi(db_path)
    _, account = create_customer_and_account(app)

    request(
        app,
        "POST",
        "/ledger/deposit",
        {
            "external_ref": "dep-3",
            "account_id": account["id"],
            "amount": "50.00",
            "currency": "USD",
            "description": "Funding",
        },
    )
    ok_status, _ = request(
        app,
        "POST",
        "/ledger/withdraw",
        {
            "external_ref": "wd-ok",
            "account_id": account["id"],
            "amount": "60.00",
            "currency": "USD",
            "description": "Allowed overdraft",
        },
    )
    fail_status, fail = request(
        app,
        "POST",
        "/ledger/withdraw",
        {
            "external_ref": "wd-fail",
            "account_id": account["id"],
            "amount": "20.01",
            "currency": "USD",
            "description": "Too much",
        },
    )

    assert ok_status == 201
    assert fail_status == 409
    assert fail["detail"] == "Insufficient funds"


def test_transfer_and_reversal_are_correct(setup_db):
    db_path, _ = setup_db
    app = JsonApi(db_path)
    _, source = create_customer_and_account(app)
    _, bob = request(app, "POST", "/customers", {"external_ref": "cust-2", "name": "Bob"})
    _, destination = request(
        app,
        "POST",
        "/accounts",
        {
            "customer_id": bob["id"],
            "account_number": "CHK-2",
            "name": "Bob Checking",
            "currency": "USD",
            "overdraft_limit": "0.00",
        },
    )

    request(
        app,
        "POST",
        "/ledger/deposit",
        {
            "external_ref": "dep-4",
            "account_id": source["id"],
            "amount": "90.00",
            "currency": "USD",
            "description": "Initial funding",
        },
    )
    status, transfer = request(
        app,
        "POST",
        "/ledger/transfer",
        {
            "external_ref": "trf-1",
            "source_account_id": source["id"],
            "destination_account_id": destination["id"],
            "amount": "30.00",
            "currency": "USD",
            "description": "P2P transfer",
        },
    )
    assert status == 201

    _, source_balance = request(app, "GET", f"/accounts/{source['id']}/balance")
    _, destination_balance = request(app, "GET", f"/accounts/{destination['id']}/balance")
    assert source_balance["balance"] == "60.00"
    assert destination_balance["balance"] == "30.00"

    reversal_status, _ = request(
        app,
        "POST",
        "/ledger/reverse",
        {
            "external_ref": "rev-1",
            "transaction_id": transfer["id"],
            "description": "Undo transfer",
        },
    )
    assert reversal_status == 201

    _, source_after = request(app, "GET", f"/accounts/{source['id']}/balance")
    _, destination_after = request(app, "GET", f"/accounts/{destination['id']}/balance")
    assert source_after["balance"] == "90.00"
    assert destination_after["balance"] == "0.00"


def test_statement_by_date_range(setup_db):
    db_path, _ = setup_db
    app = JsonApi(db_path)
    _, account = create_customer_and_account(app)

    request(
        app,
        "POST",
        "/ledger/deposit",
        {
            "external_ref": "dep-5",
            "account_id": account["id"],
            "amount": "10.00",
            "currency": "USD",
            "description": "Statement seed",
        },
    )
    status, statement = request(
        app,
        "GET",
        f"/accounts/{account['id']}/statement",
        query_string=f"start_date={date.today().isoformat()}&end_date={date.today().isoformat()}",
    )
    assert status == 200
    assert statement["opening_balance"] == "0.00"
    assert statement["closing_balance"] == "10.00"
    assert statement["entries"][0]["external_ref"] == "dep-5"
