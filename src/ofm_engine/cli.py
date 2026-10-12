"""`ofm` command-line interface (Typer)."""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
import yaml

from . import __version__
from .settings import get_settings

app = typer.Typer(
    help="OFM Engine — tools for OnlyFans managers.", no_args_is_help=True, add_completion=False
)


def _db_ready() -> None:
    s = get_settings()
    if s.is_sqlite and s.auto_create_tables:
        from .db import create_all

        create_all()


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command("gen-key")
def gen_key() -> None:
    """Generate a new master key (Fernet) for MASTER_KEYS."""
    from .security.crypto import generate_master_key

    typer.echo(generate_master_key())


@app.command("init-client")
def init_client(
    slug: Annotated[str, typer.Argument(help="tenant slug, e.g. acme-agency")],
    name: Annotated[str, typer.Option(help="agency display name")] = "",
    admin_email: Annotated[str | None, typer.Option(help="create an admin user")] = None,
    force: Annotated[bool, typer.Option(help="overwrite an existing YAML")] = False,
) -> None:
    """Scaffold clients/<slug>.yaml and create the tenant (+ optional admin) in the database."""
    import secrets

    from sqlalchemy import select

    from .db import session_scope
    from .models import User
    from .security.passwords import hash_password
    from .services.seed import ensure_tenant
    from .tenant_config import SLUG_RE, TenantConfig

    if not SLUG_RE.fullmatch(slug):
        typer.secho("invalid slug: use lowercase letters, digits and dashes", fg="red", err=True)
        raise typer.Exit(2)
    s = get_settings()
    path = Path(s.clients_dir) / f"{slug}.yaml"
    display = name or slug.replace("-", " ").title()
    if path.exists() and not force:
        typer.echo(f"exists: {path} (use --force to overwrite)")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        cfg = TenantConfig(slug=slug, name=display).model_dump(exclude={"creators"})
        path.write_text(
            "# OFM Engine tenant config — NO SECRETS here (use Settings > Integrations or env).\n"
            + yaml.safe_dump(cfg, sort_keys=False),
            encoding="utf-8",
        )
        typer.echo(f"wrote {path}")
    _db_ready()
    with session_scope() as db:
        tenant, created = ensure_tenant(db, slug, display)
        typer.echo(f"tenant {'created' if created else 'exists'}: {slug} ({tenant.id})")
        if admin_email:
            email = admin_email.strip().lower()
            if db.scalar(select(User).where(User.email == email)):
                typer.echo(f"user exists: {email}")
            else:
                pw = secrets.token_urlsafe(14)
                db.add(
                    User(
                        tenant_id=tenant.id,
                        email=email,
                        name="Admin",
                        role="admin",
                        password_hash=hash_password(pw),
                    )
                )
                typer.echo(f"admin created: {email}  password: {pw}  (shown once — change it after login)")


@app.command()
def doctor() -> None:
    """Check configuration, database connectivity and integration modes."""
    from sqlalchemy import select, text

    from .db import session_scope
    from .models import Tenant
    from .services.context import load_context
    from .services.integrations import status

    s = get_settings()
    ok = True
    typer.echo(f"OFM Engine {__version__}  env={s.env}  db={'sqlite' if s.is_sqlite else 'postgres'}")
    problems = s.production_problems()
    for p in problems:
        level = "ERROR" if s.is_production else "warn "
        typer.echo(f"  [{level}] {p}")
        ok = ok and not s.is_production
    try:
        _db_ready()
        with session_scope() as db:
            db.execute(text("SELECT 1"))
            typer.echo("  [ok]    database reachable")
            for t in db.scalars(select(Tenant)).all():
                ctx = load_context(db, tenant_id=t.id)
                modes = ", ".join(f"{x['provider']}={x['mode']}" for x in status(ctx))
                typer.echo(f"  [ok]    tenant {t.slug}: {modes}")
    except Exception as exc:
        typer.echo(f"  [ERROR] database: {exc.__class__.__name__}: {exc}")
        ok = False
    typer.echo("doctor: OK" if ok else "doctor: problems found")
    if not ok:
        raise typer.Exit(1)


@app.command("seed-demo")
def seed_demo(
    slug: Annotated[str, typer.Option(help="tenant slug")] = "demo",
    stream: Annotated[bool, typer.Option(help="after seeding, emit synthetic live events")] = False,
    interval: Annotated[float, typer.Option(help="seconds between streamed events")] = 1.5,
    count: Annotated[int, typer.Option(help="number of streamed events (0 = forever)")] = 0,
    admin_password: Annotated[str | None, typer.Option(envvar="DEMO_ADMIN_PASSWORD")] = None,
    member_password: Annotated[str | None, typer.Option(envvar="DEMO_MEMBER_PASSWORD")] = None,
    known_passwords: Annotated[
        bool,
        typer.Option(
            "--known-passwords",
            help="use the documented demo password (demo-pass-2026) for both users; refused in production",
        ),
    ] = False,
) -> None:
    """Create a demo tenant with admin@demo.agency / member@demo.agency (prints generated passwords)."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import Tenant
    from .services.seed import DEMO_KNOWN_PASSWORD, synthetic_event
    from .services.seed import seed_demo as _seed

    if known_passwords:
        if get_settings().is_production:
            typer.secho("--known-passwords is refused when ENV=production", fg="red", err=True)
            raise typer.Exit(2)
        admin_password = admin_password or DEMO_KNOWN_PASSWORD
        member_password = member_password or DEMO_KNOWN_PASSWORD
    _db_ready()
    with session_scope() as db:
        res = _seed(db, slug=slug, admin_password=admin_password, member_password=member_password)
        typer.echo(f"tenant: {res.tenant.slug} ({'seeded' if res.created else 'already seeded'})")
        for email, pw in res.passwords.items():
            typer.echo(f"  {email}  password: {pw}")
        if not res.passwords:
            typer.echo("  users already exist (pass --admin-password/--member-password to reset)")
        typer.echo(f"  funnel: {get_settings().public_base_url}/apply/{slug}")
    if not stream:
        return
    rng = random.Random()
    n = 0
    typer.echo("streaming synthetic events (Ctrl+C to stop)")
    try:
        while count == 0 or n < count:
            with session_scope() as db:
                tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
                assert tenant is not None
                data = synthetic_event(db, tenant, rng)
            n += 1
            typer.echo(json.dumps(data))
            if count == 0 or n < count:
                time.sleep(interval)
    except KeyboardInterrupt:  # pragma: no cover
        pass


@app.command("import-revenue")
def import_revenue(
    csv_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    tenant: Annotated[str, typer.Option(help="tenant slug")] = "demo",
    mapping: Annotated[
        str, typer.Option(help='JSON column mapping, e.g. {"date":"Date","gross":"Earnings"}')
    ] = "{}",
) -> None:
    """Import a revenue CSV for a tenant."""
    from .db import session_scope
    from .services.context import load_context
    from .services.revenue import ImportError_, import_csv
    from .tenant_config import RevenueMapping

    _db_ready()
    with session_scope() as db:
        ctx = load_context(db, slug=tenant)
        cfg = RevenueMapping.model_validate(
            {**ctx.config.revenue_mapping.model_dump(), **json.loads(mapping)}
        )
        try:
            res = import_csv(db, ctx.tenant_id, csv_path.read_bytes(), cfg, csv_path.name)
        except ImportError_ as exc:
            typer.secho(f"import failed: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc
    typer.echo(json.dumps(res.as_dict()))


@app.command("send-test-event")
def send_test_event(
    tenant: Annotated[str, typer.Option(help="tenant slug")] = "demo",
    provider: Annotated[str, typer.Option(help="meta_capi | tiktok | ghl | llm")] = "meta_capi",
) -> None:
    """Send a test event through an integration (mock unless credentials are configured)."""
    from .db import session_scope
    from .services.context import load_context
    from .services.integrations import PROVIDERS, test

    if provider not in PROVIDERS:
        typer.secho(f"unknown provider {provider}", fg="red", err=True)
        raise typer.Exit(2)
    _db_ready()
    with session_scope() as db:
        res = test(load_context(db, slug=tenant), provider)
    typer.echo(json.dumps(res))
    if not res["ok"]:
        raise typer.Exit(1)


@app.command("rotate-keys")
def rotate_keys() -> None:
    """Re-wrap every stored data key with the primary MASTER_KEYS entry (prepend new key first)."""
    from .db import session_scope
    from .services.secrets import rotate_all

    _db_ready()
    with session_scope() as db:
        n = rotate_all(db)
    typer.echo(f"re-wrapped {n} encrypted values with the primary master key")


@app.command()
def worker(
    once: Annotated[bool, typer.Option(help="drain due jobs and exit")] = False,
) -> None:
    """Run the background job worker (GHL, CAPI, TikTok, webhooks, alerts)."""
    from .logging_setup import configure_logging
    from .services import jobs

    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    _db_ready()
    if once:
        typer.echo(f"processed {jobs.drain()} jobs")
        return
    jobs.run_worker()  # pragma: no cover


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
    workers: Annotated[int, typer.Option()] = 1,
    forwarded_allow_ips: Annotated[
        str,
        typer.Option(
            envvar="FORWARDED_ALLOW_IPS",
            help="proxies trusted for X-Forwarded-For/Proto (comma list; * behind a managed LB)",
        ),
    ] = "127.0.0.1",
) -> None:
    """Run the HTTP server (uvicorn)."""
    import uvicorn  # pragma: no cover

    uvicorn.run(
        "ofm_engine.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,  # pragma: no cover
        workers=workers,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
    )


def alembic_ini() -> Path:
    """Locate alembic.ini: $ALEMBIC_CONFIG, the source checkout, then the copy packaged in the wheel."""
    here = Path(__file__).resolve().parent
    candidates = [
        Path(os.environ["ALEMBIC_CONFIG"]) if os.environ.get("ALEMBIC_CONFIG") else None,
        here.parents[1] / "alembic.ini",  # repo checkout: <root>/src/ofm_engine/cli.py
        here / "_migrations" / "alembic.ini",  # wheel / Docker image
    ]
    for c in candidates:
        if c is not None and c.is_file():
            return c
    raise typer.BadParameter("alembic.ini not found (set ALEMBIC_CONFIG)")


@app.command()
def migrate(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """Apply Alembic migrations (Postgres / production)."""
    from alembic import command
    from alembic.config import Config

    ini = alembic_ini()
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    command.upgrade(cfg, revision)
    typer.echo(f"migrated to {revision}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
