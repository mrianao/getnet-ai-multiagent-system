"""Issue, list and revoke API credentials (local SQLite token store).

    python -m scripts.issue_token issue --role merchant --merchant cliente1988 --subject mariana
    python -m scripts.issue_token issue --role operator --subject op-ana --ttl-hours 12
    python -m scripts.issue_token list
    python -m scripts.issue_token revoke <token_id | token>

The plaintext token is printed ONCE, on stdout only; the database stores its SHA-256 hash.
Treat it like a password: do not commit it, do not paste it into tickets or chat.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings
from app.security.auth import TokenStore
from app.state.db import Database
from app.tools.customer import CUSTOMERS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("issue")
    i.add_argument("--role", choices=["merchant", "operator"], required=True)
    i.add_argument("--merchant", help="merchant id (required for --role merchant)")
    i.add_argument("--subject", required=True, help="who/what will use the token")
    i.add_argument("--ttl-hours", type=float, default=None)
    i.add_argument("--label", default="")
    i.add_argument("--quiet", action="store_true", help="print only the token")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("token_or_id")
    args = ap.parse_args(argv)

    s = get_settings()
    store = TokenStore(Database(s.state_db_path), known_merchants=set(CUSTOMERS))
    if args.cmd == "issue":
        try:
            token, rec = store.issue(args.subject, args.role, args.merchant, args.ttl_hours or s.token_ttl_hours, args.label)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if args.quiet:
            print(token)
        else:
            print(json.dumps(rec, indent=1), file=sys.stderr)
            print(f"\nTOKEN (shown once, store it securely):\n{token}")
    elif args.cmd == "list":
        print(json.dumps(store.list(), indent=1))
    elif args.cmd == "revoke":
        ok = store.revoke(args.token_or_id)
        print("revoked" if ok else "not found or already revoked")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
