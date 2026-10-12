import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", sorted((ROOT / "integrations" / "n8n").glob("*.json")))
def test_n8n_workflows_are_importable(path):
    wf = json.loads(path.read_text())
    assert wf["name"] and wf["nodes"] and isinstance(wf["connections"], dict)
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names))
    for n in wf["nodes"]:
        assert {"id", "name", "type", "typeVersion", "position", "parameters"} <= set(n)
        assert n["type"].startswith("n8n-nodes-base.")
    for src, outs in wf["connections"].items():
        assert src in names
        for branch in outs["main"]:
            for edge in branch:
                assert edge["node"] in names
    assert wf["active"] is False
    assert "whsec_" not in path.read_text() and "pit-" not in path.read_text()


def test_n8n_count_and_make_blueprint():
    assert len(list((ROOT / "integrations" / "n8n").glob("*.json"))) == 3
    bp = json.loads((ROOT / "integrations" / "make" / "ofm_new_lead_blueprint.json").read_text())
    assert bp["flow"][0]["module"] == "gateway:CustomWebHook" and "scenario" in bp["metadata"]
    assert (ROOT / "integrations" / "zapier" / "README.md").read_text().count("X-OFM-Signature") >= 1


def test_demo_client_yaml_valid():
    from ofm_engine.tenant_config import build_config

    cfg = build_config("demo", "Demo", ROOT / "clients", None)
    assert len(cfg.funnel.variants) >= 2 and cfg.creators


@pytest.mark.parametrize("path", sorted((ROOT / "sequences").glob("*.yaml")))
def test_sequences_compliant(path):
    seq = yaml.safe_load(path.read_text())
    for step in seq["steps"]:
        if step["channel"] == "sms" and "internal" not in step["channel"]:
            assert "STOP" in step["body"], (path.name, step)


def test_sample_csvs_import(db, tenants):
    from ofm_engine.services.revenue import import_csv
    from ofm_engine.tenant_config import RevenueMapping

    m = RevenueMapping(date="Day", creator="Model Name", campaign="Tracking Link", gross="Earnings (USD)",
                       new_subs="Subscribers", date_format="%m/%d/%Y", net="Net")
    res = import_csv(db, tenants["a"], (ROOT / "samples" / "revenue_infloww_style.csv").read_bytes(), m)
    assert res.imported == 3 and not res.errors
    m2 = RevenueMapping(date="Date", creator="Creator", campaign="Campaign", gross="Gross", net="Net",
                        new_subs="New Subs")
    assert import_csv(db, tenants["a"], (ROOT / "samples" / "revenue_onlyfans_export.csv").read_bytes(), m2).imported == 5
