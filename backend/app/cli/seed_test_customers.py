"""Seed three fictional customers who can sign in, each with five orders (development and test only).

    python -m app.cli.seed_test_customers [--reset-passwords]

Run `python -m app.cli.seed` and `python -m app.cli.seed_orders` first (the tenant and its catalogue).
Each customer has two orders delivered in October 2026 and three expected after 15 November 2026
(app/demo/test_customers.py). Passwords go to `data/dev-credentials.txt` (git-ignored) next to the other demo
accounts, never to the screen. Re-running is safe: existing accounts keep their password and existing orders
are left alone.
"""

import argparse
import secrets
import uuid

from sqlalchemy import func, select

from app.cli.seed import CREDENTIALS_FILE, ORG_SLUG
from app.config import REPO_ROOT, AppEnv, get_settings
from app.core.contact import contact_key
from app.core.security import hash_password
from app.db.models import Membership, Organization, Role, User
from app.db.session import get_engine, session_factory, tenant_session
from app.demo.test_customers import TEST_CUSTOMERS, seed_customer

HEADER = "# Local demo accounts (fictional). Do not reuse."


def _saved() -> dict[str, str]:
    if not CREDENTIALS_FILE.exists():
        return {}
    pairs = (
        line.split("\t", 1)
        for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines()
        if "\t" in line
    )
    return {email.strip(): password.strip() for email, password in pairs}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset-passwords", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.app_env not in (AppEnv.DEVELOPMENT, AppEnv.TEST):
        raise SystemExit("refusing to seed test customers outside development/test")
    engine = get_engine()
    saved = _saved()
    issued: dict[str, str] = {}
    users: dict[str, uuid.UUID] = {}
    with session_factory(engine)() as session, session.begin():
        org = session.scalar(select(Organization).where(Organization.slug == ORG_SLUG))
        if org is None:
            raise SystemExit(f"organization {ORG_SLUG!r} not found: run `python -m app.cli.seed` first")
        org_id = org.id
        for person in TEST_CUSTOMERS:
            user = session.scalar(select(User).where(func.lower(User.email) == person.email))
            # A new account, a reset, or an account whose password isn't in the file gets a fresh password.
            if user is None or args.reset_passwords or person.email not in saved:
                issued[person.email] = secrets.token_urlsafe(12)
            if user is None:
                user = User(email=person.email, display_name=person.name, password_hash="")
                session.add(user)
                session.flush()
            if person.email in issued:
                user.password_hash = hash_password(issued[person.email])
            membership = session.scalar(
                select(Membership).where(Membership.user_id == user.id, Membership.organization_id == org_id)
            )
            if membership is None:
                session.add(Membership(organization_id=org_id, user_id=user.id, role=Role.CUSTOMER.value))
            users[person.email] = user.id
    added = 0
    with tenant_session(org_id, engine) as session:
        for person in TEST_CUSTOMERS:
            added += seed_customer(session, org_id, person, users[person.email], contact_key(settings))

    if issued:
        merged = {**saved, **issued}
        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{email}\t{password}" for email, password in merged.items()]
        CREDENTIALS_FILE.write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    where = CREDENTIALS_FILE.relative_to(REPO_ROOT)
    print(f"{ORG_SLUG}: {len(TEST_CUSTOMERS)} test customers, {added} orders added.")
    print(f"Passwords: {where} ({len(issued)} set this run; the file keeps the other demo accounts).")
    for person in TEST_CUSTOMERS:
        print(f"  {person.name:<13} {person.email}")
        for order in person.orders:
            when = f"delivered {order.delivered}" if order.delivered else f"expected {order.expected}"
            print(f"    {order.number}  {order.product:<22} {order.status.value:<11} {when}")


if __name__ == "__main__":
    main()
