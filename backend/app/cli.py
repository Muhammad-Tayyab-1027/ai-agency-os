"""Command line: `python -m app.cli <command>`.

  setup --email you@example.com     create the owner account and the Manager agent
  worker                            run the background worker
  report [daily|weekly]             ask the Manager for a report now
  verify-audit                      verify the audit log hash chain
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.agents.registry import ensure_manager
from app.core.audit import audit, verify_chain
from app.core.security import hash_password
from app.db.models import User
from app.db.session import scoped_session


def cmd_setup(args) -> None:
    password = args.password or getpass.getpass("Owner password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("Password must be at least 12 characters.")
    with scoped_session(owner=True) as s:
        user = s.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
        if user is None:
            s.add(User(email=args.email.lower(), password_hash=hash_password(password), role="owner"))
            audit(s, actor_type="system", actor="cli", action="user.created", data={"email": args.email.lower()})
            print(f"Owner account created: {args.email}")
        else:
            print(f"Owner account already exists: {args.email}")
        manager = ensure_manager(s)
        print(f"Manager agent ready: {manager.key} ({manager.model})")


def cmd_worker(args) -> None:
    from app.workers.worker import main

    main()


def cmd_report(args) -> None:
    from app.orchestrator.reports import request_report

    with scoped_session(owner=True) as s:
        result = request_report(s, args.kind)
        print(f"Report requested: {type(result).__name__} {result.id}")


def cmd_verify(args) -> None:
    with scoped_session(owner=True) as s:
        print(verify_chain(s))


def main() -> None:
    parser = argparse.ArgumentParser(prog="agency")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--email", required=True)
    p.add_argument("--password", help="omit to be prompted (recommended)")
    p.set_defaults(fn=cmd_setup)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    p = sub.add_parser("report")
    p.add_argument("kind", nargs="?", default="daily", choices=["daily", "weekly"])
    p.set_defaults(fn=cmd_report)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
