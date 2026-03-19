from __future__ import annotations

from dataclasses import asdict

import json
from datetime import date
from decimal import Decimal
from http import HTTPStatus
from typing import Callable
from urllib.parse import parse_qs

from app.db import get_connection
from app.service import BALANCE_CONVENTION, BankingService, ConflictError, InsufficientFundsError, NotFoundError, ValidationError


class JsonApi:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url

    def __call__(self, environ, start_response):
        connection = get_connection(self.database_url)
        service = BankingService(connection)
        try:
            response = self.route(environ, service)
            status = f"{response['status']} {HTTPStatus(response['status']).phrase}"
            body = json.dumps(response["body"]).encode("utf-8")
            headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
            start_response(status, headers)
            return [body]
        except NotFoundError as exc:
            return self._error(start_response, 404, str(exc))
        except InsufficientFundsError as exc:
            return self._error(start_response, 409, str(exc))
        except (ValidationError, ConflictError, ValueError) as exc:
            return self._error(start_response, 400, str(exc))
        finally:
            connection.close()

    def route(self, environ, service: BankingService) -> dict:
        method = environ["REQUEST_METHOD"]
        path = environ.get("PATH_INFO", "")
        payload = self._read_json(environ)

        if method == "POST" and path == "/customers":
            customer = service.create_customer(payload["external_ref"], payload["name"])
            return {"status": 201, "body": asdict(customer)}
        if method == "POST" and path == "/accounts":
            account = service.create_account(
                customer_id=payload.get("customer_id"),
                account_number=payload["account_number"],
                name=payload["name"],
                currency=payload.get("currency", "USD"),
                overdraft_limit=Decimal(str(payload.get("overdraft_limit", "0.00"))),
            )
            body = asdict(account)
            body["overdraft_limit"] = f"{account.overdraft_limit:.2f}"
            return {"status": 201, "body": body}
        if method == "POST" and path == "/ledger/deposit":
            txn = service.post_deposit(**self._movement_kwargs(payload, account_key="account_id"))
            return {"status": 201, "body": _transaction_to_dict(txn)}
        if method == "POST" and path == "/ledger/withdraw":
            txn = service.post_withdrawal(**self._movement_kwargs(payload, account_key="account_id"))
            return {"status": 201, "body": _transaction_to_dict(txn)}
        if method == "POST" and path == "/ledger/transfer":
            txn = service.post_transfer(
                source_account_id=payload["source_account_id"],
                destination_account_id=payload["destination_account_id"],
                amount=Decimal(str(payload["amount"])),
                currency=payload.get("currency", "USD"),
                external_ref=payload["external_ref"],
                description=payload["description"],
            )
            return {"status": 201, "body": _transaction_to_dict(txn)}
        if method == "POST" and path == "/ledger/reverse":
            txn = service.post_reversal(
                transaction_id=payload["transaction_id"],
                external_ref=payload["external_ref"],
                description=payload["description"],
            )
            return {"status": 201, "body": _transaction_to_dict(txn)}
        if method == "GET" and path.startswith("/accounts/") and path.endswith("/balance"):
            account_id = int(path.split("/")[2])
            account = service.get_account(account_id)
            balance = service.get_balance(account_id)
            return {
                "status": 200,
                "body": {
                    "account_id": account.id,
                    "account_number": account.account_number,
                    "currency": account.currency,
                    "balance": f"{balance:.2f}",
                    "convention": BALANCE_CONVENTION,
                    "overdraft_limit": f"{account.overdraft_limit:.2f}",
                    "available_funds": f"{(balance + account.overdraft_limit):.2f}",
                },
            }
        if method == "GET" and path.startswith("/accounts/") and path.endswith("/statement"):
            account_id = int(path.split("/")[2])
            query = parse_qs(environ.get("QUERY_STRING", ""))
            start_date = date.fromisoformat(query["start_date"][0])
            end_date = date.fromisoformat(query["end_date"][0])
            return {"status": 200, "body": service.get_statement(account_id, start_date, end_date)}
        raise NotFoundError("Route not found")

    def _movement_kwargs(self, payload: dict, account_key: str) -> dict:
        return {
            account_key: payload[account_key],
            "amount": Decimal(str(payload["amount"])),
            "currency": payload.get("currency", "USD"),
            "external_ref": payload["external_ref"],
            "description": payload["description"],
        }

    def _read_json(self, environ) -> dict:
        if environ["REQUEST_METHOD"] != "POST":
            return {}
        length = int(environ.get("CONTENT_LENGTH") or 0)
        raw = environ["wsgi.input"].read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _error(self, start_response: Callable, status_code: int, detail: str):
        body = json.dumps({"detail": detail}).encode("utf-8")
        start_response(f"{status_code} {HTTPStatus(status_code).phrase}", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]


def _transaction_to_dict(transaction):
    return {
        "id": transaction.id,
        "external_ref": transaction.external_ref,
        "transaction_type": transaction.transaction_type,
        "description": transaction.description,
        "currency": transaction.currency,
        "posted_at": transaction.posted_at,
        "reversal_of_transaction_id": transaction.reversal_of_transaction_id,
        "entries": [
            {
                "id": entry.id,
                "account_id": entry.account_id,
                "direction": entry.direction,
                "amount": f"{entry.amount:.2f}",
                "posted_at": entry.posted_at,
            }
            for entry in transaction.entries
        ],
    }
