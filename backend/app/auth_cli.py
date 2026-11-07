"""Dashboard password management (run from the backend folder).

    ..\\.venv\\Scripts\\python.exe -m app.auth_cli set-password   # set or change the password
    ..\\.venv\\Scripts\\python.exe -m app.auth_cli logout-all     # end every dashboard session
    ..\\.venv\\Scripts\\python.exe -m app.auth_cli show-token     # API token for MCP/scripts

Changing the password also ends all existing sessions.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.config import get_settings
from app.core.passwords import MIN_LENGTH, PasswordStore
from app.core.security import load_or_create_token
from app.persistence.database import Database


async def _logout_all() -> None:
    db = Database(get_settings().db_path)
    await db.connect()
    try:
        await db.conn.execute("DELETE FROM sessions")
        await db.conn.commit()
    finally:
        await db.close()


def set_password() -> None:
    settings = get_settings()
    first = getpass.getpass(f"Νέος κωδικός dashboard (τουλάχιστον {MIN_LENGTH} χαρακτήρες): ")
    if len(first) < MIN_LENGTH:
        sys.exit(f"Ο κωδικός πρέπει να έχει τουλάχιστον {MIN_LENGTH} χαρακτήρες.")
    if getpass.getpass("Ξανά τον ίδιο κωδικό: ") != first:
        sys.exit("Οι κωδικοί δεν ταιριάζουν.")
    PasswordStore(settings.password_path).set(first)
    asyncio.run(_logout_all())
    print(f"Ο κωδικός αποθηκεύτηκε ({settings.password_path}). Όλες οι παλιές συνδέσεις έληξαν.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["set-password", "logout-all", "show-token"])
    args = parser.parse_args()
    if args.command == "set-password":
        set_password()
    elif args.command == "logout-all":
        asyncio.run(_logout_all())
        print("Όλες οι συνδέσεις του dashboard έληξαν.")
    else:
        print(load_or_create_token(get_settings().token_path))


if __name__ == "__main__":
    main()
