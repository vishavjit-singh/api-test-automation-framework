import json

import httpx
import pytest

from apitest.xray import aggregate, build_execution, load_mapping, upload

MAPPING = {"project": "DEMO", "test_plan": "DEMO-100",
           "cases": {"API-TC-001": {"key": "DEMO-101"}, "API-TC-002": {"key": "DEMO-102"},
                     "API-TC-003": {"key": "DEMO-103"}, "API-TC-004": {"key": "DEMO-104"}}}


def item(case, outcome, param=None, defects=(), t=1_800_000_000.0):
    return {"case": case, "nodeid": f"tests/api/t.py::f[{param}]" if param else f"tests/api/t.py::{case}",
            "param": param, "outcome": outcome, "message": "boom" if outcome == "failed" else "",
            "defects": list(defects), "start": t, "finish": t + 1, "tiers": ["negative"]}


def test_parametrised_items_fold_into_one_case_with_iterations():
    results = [item("API-TC-001", "passed", "a"), item("API-TC-001", "failed", "b"), item("API-TC-002", "passed")]
    execution, unmapped = build_execution(results, MAPPING, "inproc")
    t1, t2 = execution["tests"]
    assert t1["testKey"] == "DEMO-101" and t1["status"] == "FAILED"
    assert [i["status"] for i in t1["iterations"]] == ["PASSED", "FAILED"]
    assert "f[b]: failed (boom)" in t1["comment"]
    assert t2["status"] == "PASSED" and "iterations" not in t2
    assert execution["info"]["testPlanKey"] == "DEMO-100" and execution["info"]["testEnvironments"] == ["inproc"]
    assert unmapped == []


def test_status_mapping_cloud_and_server():
    results = [item("API-TC-001", "passed"), item("API-TC-002", "skipped"),
               item("API-TC-003", "xfailed", defects=["DEMO-301"]), item("API-TC-004", "xpassed")]
    cloud, _ = build_execution(results, MAPPING, "local")
    assert [t["status"] for t in cloud["tests"]] == ["PASSED", "TODO", "FAILED", "FAILED"]
    assert cloud["tests"][2]["defects"] == ["DEMO-301"]
    server, _ = build_execution(results, MAPPING, "local", flavor="server")
    assert [t["status"] for t in server["tests"]] == ["PASS", "TODO", "FAIL", "FAIL"]


def test_unmapped_cases_are_reported_not_invented():
    execution, unmapped = build_execution([item("API-TC-999", "passed")], MAPPING, "local")
    assert execution["tests"] == [] and unmapped == ["API-TC-999"]


def test_aggregate_time_window():
    agg = aggregate([item("API-TC-001", "passed", "a", t=10.0), item("API-TC-001", "passed", "b", t=20.0)])
    assert agg["API-TC-001"]["start"] == 10.0 and agg["API-TC-001"]["finish"] == 21.0


def test_mapping_file(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("project: DEMO\ncases:\n  API-TC-001: {key: DEMO-1}\n  API-TC-002: {key: DEMO-1}\n")
    with pytest.raises(ValueError, match="mapped twice"):
        load_mapping(p)
    assert load_mapping(tmp_path / "missing.yaml")["cases"] == {}


def test_repo_mapping_covers_every_case_once():
    m = load_mapping("xray/mapping.yaml")
    assert len(m["cases"]) == 55 and m["test_plan"] == "DEMO-100"


def test_upload_authenticates_then_imports():
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers.get("authorization"), json.loads(req.content)))
        if req.url.path.endswith("/authenticate"):
            return httpx.Response(200, json="tok-123")
        return httpx.Response(200, json={"key": "DEMO-900"})

    out = upload({"info": {}, "tests": []}, "id", "secret", transport=httpx.MockTransport(handler))
    assert out == {"key": "DEMO-900"}
    assert seen[0][0] == "/api/v2/authenticate" and seen[0][2] == {"client_id": "id", "client_secret": "secret"}
    assert seen[1][0] == "/api/v2/import/execution" and seen[1][1] == "Bearer tok-123"
