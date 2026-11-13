#!/usr/bin/env python3
"""Add or reset a login for the City Watch app. Run interactively:

    python3 scripts/add_user.py <username>

Prompts for the password with getpass (never typed in cleartext on screen,
never passed as a CLI argument, never logged). Writes/updates
auth_users.json at the project root, which is gitignored and holds only
salted PBKDF2 hashes, never a plaintext password.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.auth import set_user_password


def main():
    if len(sys.argv) != 2:
        print("usage: python3 scripts/add_user.py <username>", file=sys.stderr)
        sys.exit(1)
    username = sys.argv[1].strip()
    password = getpass.getpass(f"New password for '{username}': ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords didn't match -- nothing changed.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Use at least 8 characters -- nothing changed.", file=sys.stderr)
        sys.exit(1)
    set_user_password(username, password)
    print(f"Saved. '{username}' can now sign in at /login.")


if __name__ == "__main__":
    main()
