"""Map pytest results to Jira/Xray test issues and build an Xray JSON execution import.

Every test function carries ``@pytest.mark.case("API-TC-0xx")``. ``xray/mapping.yaml`` maps those
stable case IDs to Xray test issue keys, so renaming a test function never breaks traceability.
Parametrised tests become one Xray test with one iteration per parameter set.

The output follows the Xray JSON import format (Cloud ``PASSED/FAILED/TODO`` or Server/DC
``PASS/FAIL/TODO``). :func:`upload` posts it to Xray Cloud; it is never called by the suite itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

STATUS = {
    "cloud": {"passed": "PASSED", "failed": "FAILED", "skipped": "TODO"},
    "server": {"passed": "PASS", "failed": "FAIL", "skipped": "TODO"},
}
XRAY_CLOUD = "https://xray.cloud.getxray.app"


def load_mapping(path: str | Path) -> dict:
    path = Path(path)
    raw = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    cases = raw.get("cases", {})
    keys = [v["key"] for v in cases.values()]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"Xray keys mapped twice: {sorted(dupes)}")
    return {"project": raw.get("project"), "test_plan": raw.get("test_plan"), "cases": cases}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def aggregate(results: list[dict]) -> dict[str, dict]:
    """Fold per-item results into one entry per case ID (parametrised items become iterations)."""
    out: dict[str, dict] = {}
    for r in results:
        c = out.setdefault(r["case"], {"case": r["case"], "title": r.get("title", ""), "tiers": r.get("tiers", []),
                                       "start": r["start"], "finish": r["finish"], "items": [], "defects": []})
        c["start"], c["finish"] = min(c["start"], r["start"]), max(c["finish"], r["finish"])
        c["items"].append(r)
        for d in r.get("defects", []):
            if d not in c["defects"]:
                c["defects"].append(d)
    for c in out.values():
        outcomes = {i["outcome"] for i in c["items"]}
        c["outcome"] = "failed" if outcomes & {"failed", "xfailed", "xpassed"} else (
            "passed" if "passed" in outcomes else "skipped")
    return out


def build_execution(results: list[dict], mapping: dict, environment: str, flavor: str = "cloud",
                    summary: str | None = None) -> tuple[dict, list[str]]:
    """Return (Xray import JSON, case IDs that have no mapping)."""
    status = STATUS[flavor]
    cases = aggregate(results)
    tests, unmapped = [], []
    for case_id in sorted(cases):
        c = cases[case_id]
        entry = mapping["cases"].get(case_id)
        if not entry:
            unmapped.append(case_id)
            continue
        comments = []
        for i in c["items"]:
            if i["outcome"] != "passed":
                comments.append(f"{i['nodeid']}: {i['outcome']}" + (f" ({i['message']})" if i.get("message") else ""))
        test = {"testKey": entry["key"], "start": _iso(c["start"]), "finish": _iso(c["finish"]),
                "status": status[c["outcome"]], "comment": "\n".join(comments) or f"{case_id}: all checks passed"}
        if c["defects"]:
            test["defects"] = c["defects"]
        if len(c["items"]) > 1:
            test["iterations"] = [{"name": i.get("param") or i["nodeid"],
                                   "parameters": [{"name": "case", "value": i.get("param") or ""}],
                                   "status": status["passed" if i["outcome"] == "passed" else
                                                    "skipped" if i["outcome"] == "skipped" else "failed"]}
                                  for i in c["items"]]
        tests.append(test)
    starts = [c["start"] for c in cases.values()] or [0]
    finishes = [c["finish"] for c in cases.values()] or [0]
    info = {"summary": summary or f"API regression · {environment}", "description":
            f"{len(tests)} Xray tests from {len(results)} pytest items", "startDate": _iso(min(starts)),
            "finishDate": _iso(max(finishes)), "testEnvironments": [environment]}
    if mapping.get("test_plan"):
        info["testPlanKey"] = mapping["test_plan"]
    if mapping.get("project"):
        info["project"] = mapping["project"]
    return {"info": info, "tests": tests}, unmapped


def upload(execution: dict, client_id: str, client_secret: str, base_url: str = XRAY_CLOUD,
           transport: httpx.BaseTransport | None = None) -> dict:
    """Authenticate against Xray Cloud and import the execution. Returns Xray's response JSON."""
    with httpx.Client(base_url=base_url, transport=transport, timeout=30) as http:
        auth = http.post("/api/v2/authenticate", json={"client_id": client_id, "client_secret": client_secret})
        auth.raise_for_status()
        token = auth.json()
        resp = http.post("/api/v2/import/execution", json=execution, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.json()
