import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_connection
from app.service import BankingService, bootstrap_database


if __name__ == "__main__":
    bootstrap_database()
    connection = get_connection()
    try:
        account = BankingService(connection).ensure_internal_settlement_account()
        print(f"Initialized internal account {account.account_number} (id={account.id})")
    finally:
        connection.close()
