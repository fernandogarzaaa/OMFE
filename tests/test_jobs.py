import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from conftest import apply_body


def _jobs(db, kind=None):
    from ofm_engine.models import OutboxJob

    q = db.query(OutboxJob)
    if kind:
        q = q.filter_by(kind=kind)
    db.expire_all()
    return q.all()


def test_worker_processes_lead_pipeline_mock(client, admin_a, db):
    from ofm_engine.models import ConversionEvent, IntegrationLog, Lead
    from ofm_engine.services import jobs

    lead_id = client.post("/api/v1/public/alpha/apply", json=apply_body()).json()["id"]
    n = jobs.drain()
    assert n >= 3
    statuses = {j.kind: j.status for j in _jobs(db)}
    assert statuses["ghl.upsert_lead"] == "succeeded"
    assert statuses["ghl.opportunity"] == "succeeded"
    assert statuses["meta_capi.send"] == "succeeded"
    db.expire_all()
    lead = db.get(Lead, uuid.UUID(lead_id))
    assert lead.ghl_contact_id.startswith("mock_c_") and lead.ghl_opportunity_id.startswith("mock_o_")
    assert db.query(ConversionEvent).filter_by(name="Lead").one().capi_status == "sent"
    assert db.query(IntegrationLog).filter_by(adapter="ghl", ok=True).count() >= 3
    assert admin_a.get(f"/api/v1/leads/{lead_id}").json()["ghl_contact_id"] == lead.ghl_contact_id


def test_enqueue_idempotent(db, tenants):
    from ofm_engine.services import jobs

    a = jobs.enqueue(db, tenants["a"], "alert.send", {"text": "x"}, "same-key")
    b = jobs.enqueue(db, tenants["a"], "alert.send", {"text": "x"}, "same-key")
    db.commit()
    assert a is not None and b is None
    assert len(_jobs(db, "alert.send")) == 1
    with pytest.raises(ValueError):
        jobs.enqueue(db, tenants["a"], "nope.kind", {}, "k")


def test_failure_rolls_back_and_retries_with_backoff(db, tenants, monkeypatch):
    from ofm_engine.models import Creator, IntegrationLog, OutboxJob
    from ofm_engine.services import jobs

    def boom(ctx, job):
        ctx.db.add(Creator(tenant_id=ctx.tenant_id, slug="partial", display_name="should rollback"))
        ctx.db.flush()
        raise RuntimeError("provider exploded token=abc123secret")

    monkeypatch.setitem(jobs.HANDLERS, "alert.send", boom)
    job = jobs.enqueue(db, tenants["a"], "alert.send", {"text": "x"}, "fail-1", max_attempts=2)
    db.commit()
    assert jobs.run_once() == 1
    db.expire_all()
    j = db.get(OutboxJob, job.id)
    assert j.status == "retry" and j.attempts == 1 and "exploded" in j.last_error
    assert "abc123secret" not in j.last_error
    run_after = j.run_after if j.run_after.tzinfo else j.run_after.replace(tzinfo=UTC)
    assert run_after > datetime.now(UTC)
    assert db.query(Creator).filter_by(slug="partial").count() == 0  # side effects rolled back
    assert db.query(IntegrationLog).filter_by(ok=False).count() == 1  # failure recorded after rollback
    assert jobs.run_once() == 0  # not yet due
    j.run_after = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    jobs.run_once()
    db.expire_all()
    j = db.get(OutboxJob, job.id)
    assert j.status == "dead" and j.attempts == 2


def test_permanent_error_goes_dead(db, tenants, monkeypatch):
    from ofm_engine.services import jobs

    def bad(ctx, job):
        raise jobs.PermanentJobError("bad payload")

    monkeypatch.setitem(jobs.HANDLERS, "alert.send", bad)
    job = jobs.enqueue(db, tenants["a"], "alert.send", {}, "perm")
    db.commit()
    assert jobs.run_job(jobs.claim(db, 10)[0]) == "dead"


def test_retry_endpoint(admin_a, db, tenants, monkeypatch):
    from ofm_engine.services import jobs

    monkeypatch.setitem(jobs.HANDLERS, "alert.send", lambda c, j: (_ for _ in ()).throw(jobs.PermanentJobError("x")))
    job = jobs.enqueue(db, tenants["a"], "alert.send", {}, "retry-me")
    db.commit()
    jobs.drain()
    assert admin_a.get("/api/v1/automations/jobs?status=dead").json()["items"][0]["id"] == str(job.id)
    r = admin_a.post(f"/api/v1/automations/jobs/{job.id}/retry")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert admin_a.post(f"/api/v1/automations/jobs/{job.id}/retry").status_code == 409
    assert admin_a.post(f"/api/v1/automations/jobs/{uuid.uuid4()}/retry").status_code == 404


def test_stale_running_job_reclaimed(db, tenants):
    from ofm_engine.services import jobs

    job = jobs.enqueue(db, tenants["a"], "alert.send", {"text": "x"}, "stale")
    job.status = "running"
    job.locked_at = datetime.now(UTC) - timedelta(seconds=jobs.LEASE_SECONDS + 5)
    db.commit()
    assert jobs.claim(db, 10) == [job.id]


def test_backoff_monotonic():
    from ofm_engine.services.jobs import BACKOFF_CAP, backoff_seconds

    vals = [backoff_seconds(i) for i in range(1, 15)]
    assert vals == sorted(vals) and vals[-1] == BACKOFF_CAP


def test_webhook_delivery_signed_live(admin_a, client, db, monkeypatch):
    from ofm_engine.security import signing
    from ofm_engine.services import jobs
    from ofm_engine.settings import reset_settings_cache

    sub = admin_a.post("/api/v1/automations/webhooks",
                       json={"url": "https://hooks.slack.com/services/T/B/x", "events": ["lead.created"]}).json()
    secret = sub["secret"]
    assert secret.startswith("whsec_")
    assert "secret" not in admin_a.get("/api/v1/automations/webhooks").json()["items"][0]
    monkeypatch.setenv("OUTBOUND_WEBHOOKS_LIVE", "true")
    reset_settings_cache()
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    with respx.mock(assert_all_called=True) as rx:
        route = rx.post("https://hooks.slack.com/services/T/B/x").mock(return_value=httpx.Response(200))
        jobs.drain()
        req = route.calls[0].request
    ts = signing.verify(secret, req.content, req.headers[signing.SIGNATURE_HEADER])
    assert ts > 0
    body = json.loads(req.content)
    assert body["event"] == "lead.created" and body["data"]["name"] == "Ava Test"
    assert req.headers["X-OFM-Event"] == "lead.created"
    assert all(j.status == "succeeded" for j in _jobs(db, "webhook.deliver"))


def test_webhook_delivery_failure_retries(admin_a, client, db, monkeypatch):
    from ofm_engine.services import jobs
    from ofm_engine.settings import reset_settings_cache

    admin_a.post("/api/v1/automations/webhooks", json={"url": "https://hooks.slack.com/services/z", "events": ["*"]})
    monkeypatch.setenv("OUTBOUND_WEBHOOKS_LIVE", "true")
    reset_settings_cache()
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    with respx.mock() as rx:
        rx.post("https://hooks.slack.com/services/z").mock(return_value=httpx.Response(503))
        jobs.drain()
    assert _jobs(db, "webhook.deliver")[0].status == "retry"


def test_webhook_mock_mode_and_delete(admin_a, client, db):
    from ofm_engine.services import jobs

    sub = admin_a.post("/api/v1/automations/webhooks",
                       json={"url": "https://hooks.slack.com/services/m", "events": ["*"]}).json()
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    jobs.drain()
    assert _jobs(db, "webhook.deliver")[0].status == "succeeded"
    assert admin_a.delete(f"/api/v1/automations/webhooks/{sub['id']}").status_code == 200
    assert admin_a.delete(f"/api/v1/automations/webhooks/{sub['id']}").status_code == 404


def test_webhook_create_rejects_unsafe(admin_a):
    for url in ("http://hooks.slack.com/x", "https://127.0.0.1/x", "https://user:pw@hooks.slack.com/x",
                "https://localhost/x"):
        r = admin_a.post("/api/v1/automations/webhooks", json={"url": url, "events": ["*"]})
        assert r.status_code == 422, url
    r = admin_a.post("/api/v1/automations/webhooks", json={"url": "https://hooks.slack.com/x", "events": ["bogus"]})
    assert r.status_code == 422


def test_ghl_live_mode_with_respx(admin_a, client, db):
    from ofm_engine.services import jobs

    admin_a.put("/api/v1/integrations/ghl", json={"fields": {"token": "pit-live-token-123456", "location_id": "LOC1"}})
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as rx:
        rx.post("https://services.leadconnectorhq.com/contacts/upsert").mock(
            return_value=httpx.Response(200, json={"new": True, "contact": {"id": "C1"}}))
        rx.post("https://services.leadconnectorhq.com/contacts/C1/tags").mock(return_value=httpx.Response(201, json={}))
        rx.post("https://services.leadconnectorhq.com/contacts/C1/notes").mock(
            return_value=httpx.Response(201, json={"note": {"id": "N1"}}))
        opp = rx.post("https://services.leadconnectorhq.com/opportunities/").mock(
            return_value=httpx.Response(201, json={"opportunity": {"id": "O1"}}))
        rx.post(url__regex=r"https://graph\.facebook\.com/.*").mock(
            return_value=httpx.Response(200, json={"events_received": 1}))
        jobs.drain()
        upsert = rx.calls[0].request
    assert upsert.headers["Authorization"] == "Bearer pit-live-token-123456"
    assert upsert.headers["Version"] == "2021-07-28"
    assert json.loads(upsert.content)["locationId"] == "LOC1"
    assert json.loads(opp.calls[0].request.content)["contactId"] == "C1"
    from ofm_engine.models import IntegrationLog

    assert "pit-live-token" not in json.dumps([log.detail for log in db.query(IntegrationLog).all()])


def test_ghl_4xx_is_permanent(admin_a, client, db):
    from ofm_engine.services import jobs

    admin_a.put("/api/v1/integrations/ghl", json={"fields": {"token": "pit-live-token-123456", "location_id": "LOC1"}})
    client.post("/api/v1/public/alpha/apply", json=apply_body())
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as rx:
        rx.post("https://services.leadconnectorhq.com/contacts/upsert").mock(
            return_value=httpx.Response(401, json={"message": "Invalid JWT"}))
        rx.post(url__regex=r"https://graph\.facebook\.com/.*").mock(return_value=httpx.Response(200, json={}))
        jobs.drain()
    assert _jobs(db, "ghl.upsert_lead")[0].status == "dead"


def test_stage_change_updates_ghl_and_webhooks(client, admin_a, db):
    from ofm_engine.services import jobs

    lead_id = client.post("/api/v1/public/alpha/apply", json=apply_body()).json()["id"]
    jobs.drain()
    r = admin_a.patch(f"/api/v1/leads/{lead_id}", json={"stage": "signed"})
    assert r.json()["stage"] == "signed"
    jobs.drain()
    opp_jobs = _jobs(db, "ghl.opportunity")
    assert len(opp_jobs) == 2 and all(j.status == "succeeded" for j in opp_jobs)
    pipe = admin_a.get("/api/v1/pipeline").json()
    assert [s["key"] for s in pipe["stages"]] == ["new", "contacted", "call_booked", "signed", "lost"]
    assert next(s for s in pipe["stages"] if s["key"] == "signed")["count"] == 1


def test_in_process_worker_thread(db, tenants):
    import time

    from ofm_engine.services import jobs

    jobs.enqueue(db, tenants["a"], "alert.send", {"text": "hi"}, "thread")
    db.commit()
    t, stop = jobs.start_in_process_worker()
    for _ in range(50):
        if _jobs(db, "alert.send")[0].status == "succeeded":
            break
        time.sleep(0.05)
    stop.set()
    t.join(2)
    assert _jobs(db, "alert.send")[0].status == "succeeded"
