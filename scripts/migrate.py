import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service import bootstrap_database


if __name__ == "__main__":
    bootstrap_database()
    print("Applied migrations and ensured internal settlement account exists.")
