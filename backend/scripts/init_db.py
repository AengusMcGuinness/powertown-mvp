from __future__ import annotations

from backend.app.db import init_db, DB_URL


def main() -> None:
    init_db()
    print(f"Database initialized at: {DB_URL}")


if __name__ == "__main__":
    main()
