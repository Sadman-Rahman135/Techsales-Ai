"""Seed the fictional catalogue, customers, orders and repairs (US-05, ADR-031; development and test only).

    python -m app.cli.seed_orders [--organization techcare]

Run `python -m app.cli.seed` first, so the demo customer account owns order TC-100001. Re-running is safe: an
organization that already has orders only gets what is missing (repairs, pickup stores). Demo orders and
repairs to try: app/demo/orders.py `DEMO_ORDERS`, `DEMO_REPAIRS`.
"""

import argparse

from sqlalchemy import select

from app.config import AppEnv, get_settings
from app.core.contact import contact_key
from app.db.models import Organization
from app.db.session import get_engine, session_factory, tenant_session
from app.demo.orders import DEMO_ORDERS, DEMO_REPAIRS, backfill_pickups, seed_orders, seed_repairs


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--organization", default=None, help="organization slug (default: PUBLIC_ORGANIZATION)"
    )
    args = parser.parse_args()
    settings = get_settings()
    if settings.app_env not in (AppEnv.DEVELOPMENT, AppEnv.TEST):
        raise SystemExit("refusing to seed demo orders outside development/test")
    engine = get_engine()
    slug = args.organization or settings.public_organization
    with session_factory(engine)() as session:
        org_id = session.scalar(select(Organization.id).where(Organization.slug == slug))
    if org_id is None:
        raise SystemExit(f"organization {slug!r} not found: run `python -m app.cli.seed` first")
    with tenant_session(org_id, engine) as session:
        counts = seed_orders(session, org_id, contact_key(settings))
        if not counts["orders"]:  # seeded before: add only what ADR-031 brought
            counts = {
                "repairs": seed_repairs(session, org_id),
                "pickup orders updated": backfill_pickups(session),
            }
    totals = ", ".join(f"{n} {kind}" for kind, n in counts.items())
    print(f"{slug}: {totals}.")
    print("Demo orders (fictional) to verify in the chat:")
    for demo in DEMO_ORDERS:
        print(f"  {demo.order_number}  {demo.status.value:<17} email {demo.email}  phone {demo.phone}")
    print("Demo repairs (verify with the same email as the order owner above):")
    for repair in DEMO_REPAIRS:
        owner = DEMO_ORDERS[repair.owner]
        print(
            f"  {repair.reference}  {repair.status.value:<17} {repair.product_name:<20} email {owner.email}"
        )


if __name__ == "__main__":
    main()
