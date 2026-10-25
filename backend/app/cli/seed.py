"""Seed the fictional TechCare tenant and one user per role (development and test only).

    python -m app.cli.seed [--reset-passwords]

Passwords are generated, printed once, and written to `data/dev-credentials.txt` (git-ignored). Re-running
is safe: existing users keep their password unless `--reset-passwords` is given.
"""

import argparse
import secrets

from sqlalchemy import Engine, func, select

from app.config import REPO_ROOT, AppEnv, get_settings
from app.core.security import hash_password
from app.db.models import Membership, Organization, Role, User
from app.db.session import get_engine, session_factory

ORG_SLUG = "techcare"
USERS = (
    ("morgan.admin@techcare.example", "Morgan Lee", Role.ADMIN),
    ("priya.manager@techcare.example", "Priya Shah", Role.MANAGER),
    ("sam.agent@techcare.example", "Sam Okafor", Role.EMPLOYEE),
    ("alex.customer@techcare.example", "Alex Rivera", Role.CUSTOMER),
)
CREDENTIALS_FILE = REPO_ROOT / "data" / "dev-credentials.txt"


def seed(engine: Engine, reset_passwords: bool = False) -> dict[str, str]:
    """Returns the passwords that were set in this run (email → password)."""
    issued: dict[str, str] = {}
    with session_factory(engine)() as session, session.begin():
        org = session.scalar(select(Organization).where(Organization.slug == ORG_SLUG))
        if org is None:
            org = Organization(name="TechCare", slug=ORG_SLUG, timezone="America/Chicago")
            session.add(org)
            session.flush()
        for email, name, role in USERS:
            user = session.scalar(select(User).where(func.lower(User.email) == email))
            if user is None or reset_passwords:
                password = secrets.token_urlsafe(12)
                issued[email] = password
            if user is None:
                user = User(email=email, display_name=name, password_hash=hash_password(issued[email]))
                session.add(user)
                session.flush()
            elif reset_passwords:
                user.password_hash = hash_password(issued[email])
            membership = session.scalar(
                select(Membership).where(Membership.user_id == user.id, Membership.organization_id == org.id)
            )
            if membership is None:
                session.add(Membership(organization_id=org.id, user_id=user.id, role=role.value))
            else:
                membership.role = role.value
    return issued


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset-passwords", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.app_env not in (AppEnv.DEVELOPMENT, AppEnv.TEST):
        raise SystemExit("refusing to seed demo users outside development/test")
    issued = seed(get_engine(), args.reset_passwords)
    if not issued:
        print("TechCare users already exist; passwords unchanged (use --reset-passwords).")
        return
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{email}\t{password}" for email, password in issued.items()]
    CREDENTIALS_FILE.write_text(
        "# Local demo accounts (fictional). Do not reuse.\n" + "\n".join(lines) + "\n"
    )
    print(f"Seeded TechCare. Passwords written to {CREDENTIALS_FILE.relative_to(REPO_ROOT)}:")
    for line in lines:
        print("  " + line)


if __name__ == "__main__":
    main()
