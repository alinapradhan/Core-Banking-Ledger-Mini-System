import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "banking.db")
    settlement_cash_account_number: str = os.getenv("SETTLEMENT_CASH_ACCOUNT_NUMBER", "SETTLEMENT_CASH")
    settlement_cash_account_name: str = os.getenv("SETTLEMENT_CASH_ACCOUNT_NAME", "Settlement Cash")
    settlement_cash_customer_name: str = os.getenv("SETTLEMENT_CASH_CUSTOMER_NAME", "BANK_INTERNAL")


settings = Settings()
