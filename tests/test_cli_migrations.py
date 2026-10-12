import json
import os
import re
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from ofm_engine.cli import app as cli_app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_cli_version_and_genkey():
    assert runner.invoke(cli_app, ["version"]).output.strip() == "1.0.0"
    assert len(runner.invoke(cli_app, ["gen-key"]).output.strip()) == 44


def test_seed_demo_prints_passwords_and_is_idempotent():
    r = runner.invoke(cli_app, ["seed-demo"])
    assert r.exit_code == 0, r.output
    assert "admin@example.com  password:" in r.output and "member@example.com  password:" in r.output
    r2 = runner.invoke(cli_app, ["seed-demo"])
    assert "already seeded" in r2.output and "users already exist" in r2.output
    r3 = runner.invoke(cli_app, ["seed-demo", "--admin-password", "Known-Passw0rd-123"])
    assert "Known-Passw0rd-123" in r3.output


def test_seed_demo_stream():
    r = runner.invoke(cli_app, ["seed-demo", "--stream", "--count", "3", "--interval", "0"])
    assert r.exit_code == 0, r.output
    lines = [json.loads(x) for x in r.output.splitlines() if x.startswith("{")]
    assert len(lines) == 3 and all(x["synthetic"] for x in lines)


def test_seeded_demo_login_and_kpis():
    from fastapi.testclient import TestClient

    from ofm_engine.app import create_app

    assert runner.invoke(cli_app, ["seed-demo", "--admin-password", "Known-Passw0rd-123"]).exit_code == 0
    c = TestClient(create_app())
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": "admin@example.com", "password": "Known-Passw0rd-123"}
        ).status_code
        == 200
    )
    k = c.get("/api/v1/kpis?range=30d").json()
    assert k["clicks"] > 0 and k["revenue_cents"] > 0 and k["leads"] > 0 and k["by_creator"]
    assert c.get("/apply/demo").status_code == 200


def test_init_client():
    r = runner.invoke(
        cli_app, ["init-client", "acme-agency", "--name", "Acme", "--admin-email", "boss@acme.example"]
    )
    assert r.exit_code == 0, r.output
    assert "admin created: boss@acme.example  password:" in r.output
    from ofm_engine.settings import get_settings

    y = Path(get_settings().clients_dir) / "acme-agency.yaml"
    assert y.exists() and "NO SECRETS" in y.read_text()
    r2 = runner.invoke(cli_app, ["init-client", "acme-agency", "--admin-email", "boss@acme.example"])
    assert "exists" in r2.output and "user exists" in r2.output
    assert runner.invoke(cli_app, ["init-client", "Bad Slug"]).exit_code == 2


def test_doctor_send_test_event_rotate_worker():
    runner.invoke(cli_app, ["seed-demo"])
    r = runner.invoke(cli_app, ["doctor"])
    assert r.exit_code == 0 and "tenant demo: ghl=mock" in r.output
    r = runner.invoke(cli_app, ["send-test-event", "--tenant", "demo"])
    assert r.exit_code == 0 and json.loads(r.output)["ok"] is True
    assert runner.invoke(cli_app, ["send-test-event", "--provider", "nope"]).exit_code == 2
    assert "re-wrapped" in runner.invoke(cli_app, ["rotate-keys"]).output
    assert "processed" in runner.invoke(cli_app, ["worker", "--once"]).output


def test_import_revenue_cli(tmp_path):
    runner.invoke(cli_app, ["seed-demo"])
    f = tmp_path / "rev.csv"
    f.write_text("Day,Amount\n2026-10-01,100\n")
    r = runner.invoke(cli_app, ["import-revenue", str(f), "--mapping", '{"date":"Day","gross":"Amount"}'])
    assert r.exit_code == 0 and json.loads(r.output)["imported"] == 1
    bad = tmp_path / "bad.csv"
    bad.write_text("x\n1\n")
    assert runner.invoke(cli_app, ["import-revenue", str(bad)]).exit_code == 1


def test_doctor_reports_db_failure(monkeypatch):
    from ofm_engine import db as dbmod
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", "sqlite:////nonexistent-dir/x/y.db")
    reset_settings_cache()
    dbmod.dispose_engine()
    r = runner.invoke(cli_app, ["doctor"])
    assert r.exit_code == 1 and "ERROR" in r.output


def _alembic(args, url):
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_migrations_no_drift_sqlite(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, up.stderr
    check = _alembic(["check"], url)
    assert check.returncode == 0, check.stdout + check.stderr
    assert _alembic(["downgrade", "base"], url).returncode == 0


def test_migrations_render_offline_postgres():
    out = _alembic(["upgrade", "head", "--sql"], "postgresql://u:p@localhost:5432/ofm")
    assert out.returncode == 0, out.stderr
    sql = out.stdout
    assert sql.count("CREATE TABLE") >= 20 and "UUID" in sql and "REFERENCES tenants (id)" in sql
    assert "CREATE INDEX ix_leads_tenant_stage" in sql


def test_migration_matches_models_tables():
    from ofm_engine.models import Base

    text = (ROOT / "alembic" / "versions" / "0001_initial_schema.py").read_text()
    assert set(re.findall(r"op\.create_table\(\s*['\"](\w+)['\"]", text)) == set(Base.metadata.tables)


def test_cli_migrate_command(tmp_path, monkeypatch):
    from ofm_engine.settings import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'm2.db'}")
    reset_settings_cache()
    r = runner.invoke(cli_app, ["migrate"])
    assert r.exit_code == 0, r.output
