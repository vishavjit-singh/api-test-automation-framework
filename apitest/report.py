"""Turn a run's results.json and requests.jsonl into charts and a Markdown summary."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

INK = "#0e1a2b"
MUTED = "#5b6676"
GRID = "#e3e7ee"
ACCENT = "#2a78d6"
SIGNAL = "#eda100"
BAD = "#e34948"
GOOD = "#1baf7a"
PURPLE = "#6250d6"
TIERS = ("positive", "negative", "edge", "fallback")
OUTCOME_COLORS = {"passed": GOOD, "failed": BAD, "xfailed": SIGNAL, "skipped": GRID}
OUTCOME_LABELS = {"passed": "passed", "failed": "failed", "xfailed": "known defect (xfail)", "skipped": "skipped"}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.titleweight": "bold", "axes.titlesize": 13, "axes.titlecolor": INK,
    "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150,
})


def load(reports: Path) -> tuple[dict, list[dict]]:
    results = json.loads((reports / "results.json").read_text())
    calls = [json.loads(line) for line in (reports / "requests.jsonl").read_text().splitlines() if line.strip()]
    return results, calls


def _save(fig, out: Path, name: str) -> Path:
    fig.tight_layout()
    path = out / name
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def summary(results: dict, calls: list[dict]) -> dict:
    items = results["results"]
    outcomes = Counter(i["outcome"] for i in items)
    by_tier = {t: Counter(i["outcome"] for i in items if t in i["tiers"]) for t in TIERS}
    ms = sorted(c["ms"] for c in calls if c["status"] is not None)
    return {
        "environment": results["environment"],
        "cases": len({i["case"] for i in items}),
        "items": len(items),
        "outcomes": dict(outcomes),
        "by_tier": {t: dict(v) for t, v in by_tier.items()},
        "smoke_items": sum(1 for i in items if i.get("smoke")),
        "known_defects": sorted({d for i in items for d in i.get("defects", [])}),
        "http_calls": len(calls),
        "retried_calls": sum(1 for c in calls if c["attempt"] > 1),
        "latency_ms": {"p50": round(float(np.percentile(ms, 50)), 1), "p95": round(float(np.percentile(ms, 95)), 1),
                       "max": round(max(ms), 1)} if ms else {},
        "routes": len({c["route"] for c in calls}),
        "suite_seconds": round(sum(i["duration_s"] for i in items), 2),
    }


# ------------------------------------------------------------------------------------------------ charts
def architecture(out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(13, 5.6))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 6.0)
    ax.axis("off")

    def box(x, y, w, h, title, sub, color=INK):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc="white",
                                    ec=color, lw=2))
        ax.text(x + w / 2, y + h * 0.7, title, ha="center", va="center", fontsize=11, weight="bold", color=color)
        ax.text(x + w / 2, y + h * 0.32, sub, ha="center", va="center", fontsize=8.2, color=MUTED, linespacing=1.4)

    def arrow(x1, y1, x2, y2, color=MUTED, label=None, dy=0.13):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=14, lw=1.6, color=color))
        if label:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, label, ha="center", fontsize=7.8, color=color)

    ax.text(0.15, 5.72, "api-test-automation-framework: one suite, any environment, results traced to Xray",
            fontsize=13.5, weight="bold", color=INK)
    box(0.2, 3.3, 2.3, 1.5, "pytest suite", "tests/api · 55 cases\npositive · negative\nedge · fallback")
    box(0.2, 1.1, 2.3, 1.5, "environments.yaml", "inproc · local · docker\nstaging (URL + secret\nfrom CI variables)",
        ACCENT)
    box(3.2, 3.3, 2.5, 1.5, "apitest plugin",
        "--env · fixtures · case IDs\ntier + metadata checks\nknown defects → xfail", PURPLE)
    box(3.2, 1.1, 2.5, 1.5, "TokenFactory",
        "HS256 JWT per tenant/role\nexpired · wrong aud/iss\nalg=none · tampered", PURPLE)
    box(6.4, 2.2, 2.5, 1.6, "Shared ApiClient", "one httpx client per run\ntenant-scoped views\nretry GET on 502/3/4\n"
        "records every call", PURPLE)
    box(9.6, 3.55, 3.2, 1.45, "Orders API (demo)", "Starlette · JWT · tenants\nidempotency · stock\ncarrier + fallback",
        GOOD)
    box(9.6, 1.45, 3.2, 1.45, "Reports", "results.json · JUnit XML\nXray JSON (case → DEMO-1xx)\ncharts + summary.md",
        SIGNAL)
    box(9.6, 0.05, 3.2, 1.0, "Xray Cloud", "import/execution (optional upload)", SIGNAL)
    arrow(2.5, 4.05, 3.2, 4.05)
    arrow(2.5, 1.85, 3.2, 1.85)
    arrow(5.7, 3.9, 6.4, 3.3)
    arrow(5.7, 1.85, 6.4, 2.6)
    arrow(8.9, 3.35, 9.6, 4.2, GOOD)
    ax.text(9.0, 4.55, "HTTP or\nin-process\nASGI", ha="center", fontsize=7.8, color=GOOD, linespacing=1.3)
    arrow(8.9, 2.6, 9.6, 2.15, SIGNAL)
    arrow(11.2, 1.45, 11.2, 1.05, SIGNAL)
    ax.text(6.4, 1.55, "fault hooks (non-prod only):\ncarrier timeout / 5xx / malformed", fontsize=8, color="#9a6a00",
            va="top")
    return _save(fig, out, "architecture.png")


def results_by_tier(results: dict, out: Path) -> Path:
    items = results["results"]
    fig, ax = plt.subplots(figsize=(10, 3.6))
    y = np.arange(len(TIERS))
    left = np.zeros(len(TIERS))
    for outcome in ("passed", "xfailed", "failed", "skipped"):
        vals = np.array([sum(1 for i in items if t in i["tiers"] and i["outcome"] == outcome) for t in TIERS])
        if vals.sum() == 0:
            continue
        ax.barh(y, vals, left=left, color=OUTCOME_COLORS[outcome], label=OUTCOME_LABELS[outcome], height=0.6)
        for k, v in enumerate(vals):
            if v:
                ax.text(left[k] + v / 2, k, str(v), ha="center", va="center", fontsize=9,
                        color="white" if outcome in ("passed", "failed") else INK)
        left += vals
    ax.set_yticks(y, TIERS)
    ax.invert_yaxis()
    ax.grid(axis="x", color=GRID)
    ax.set_xlabel("pytest items (parametrised cases count once per parameter set)")
    o = Counter(i["outcome"] for i in items)
    ax.set_title(f"Results by tier · env={results['environment']} · {len(items)} items, "
                 f"{o.get('passed', 0)} passed, {o.get('xfailed', 0)} known defect, {o.get('failed', 0)} failed")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    return _save(fig, out, "results_by_tier.png")


def coverage(results: dict, calls: list[dict], out: Path) -> Path:
    tiers_of = {i["nodeid"]: i["tiers"] for i in results["results"]}
    grid: dict[str, Counter] = defaultdict(Counter)
    seen = set()
    for c in calls:
        node = c["test"]
        if not node or node not in tiers_of or c["route"].startswith("/_test"):
            continue
        for t in tiers_of[node]:
            if (node, c["route"], t) not in seen:
                seen.add((node, c["route"], t))
                grid[f"{c['method']} {c['route']}"][t] += 1
    routes = sorted(grid, key=lambda r: (r.split(" ", 1)[1], r))
    m = np.array([[grid[r][t] for t in TIERS] for r in routes])
    fig, ax = plt.subplots(figsize=(8.6, 0.42 * len(routes) + 1.3))
    ax.imshow(m, cmap="Blues", aspect="auto", vmin=0, vmax=max(1, m.max()) * 1.15)
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            ax.text(j, i, m[i, j] or "·", ha="center", va="center", fontsize=9,
                    color="white" if m[i, j] > m.max() * 0.6 else INK)
    ax.set_xticks(range(len(TIERS)), TIERS)
    ax.set_yticks(range(len(routes)), routes, fontsize=9)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Endpoint × tier coverage (tests that called the route)")
    return _save(fig, out, "coverage.png")


def latency(calls: list[dict], environment: str, out: Path) -> Path:
    by = defaultdict(list)
    for c in calls:
        if c["status"] is not None and not c["route"].startswith("/_test"):
            by[f"{c['method']} {c['route']}"].append(c["ms"])
    routes = sorted(by, key=lambda r: np.percentile(by[r], 50))
    p50 = [np.percentile(by[r], 50) for r in routes]
    p95 = [np.percentile(by[r], 95) for r in routes]
    y = np.arange(len(routes))
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(routes) + 1.4))
    ax.barh(y - 0.18, p50, height=0.36, color=ACCENT, label="p50")
    ax.barh(y + 0.18, p95, height=0.36, color=PURPLE, label="p95")
    for k in range(len(routes)):
        ax.text(p95[k] * 1.06, k + 0.18, f"{p95[k]:.0f} ms" if p95[k] >= 10 else f"{p95[k]:.1f} ms", va="center",
                fontsize=8, color=INK)
    ax.set_xscale("log")
    ax.set_yticks(y, [f"{r}  (n={len(by[r])})" for r in routes], fontsize=9)
    ax.set_xlim(min(p50) * 0.5, max(p95) * 3)
    ax.grid(axis="x", color=GRID)
    note = " · quote p95 = injected carrier timeouts, cut off at 300 ms" if any("quote" in r for r in routes) else ""
    ax.set_xlabel("client-side latency, ms (log scale)" + note)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.legend(frameon=False, loc="lower right")
    ax.set_title(f"Latency per endpoint as seen by the suite (env={environment})")
    return _save(fig, out, "latency.png")


def markdown(s: dict, results: dict) -> str:
    lines = [f"# API regression run · `{s['environment']}`", "",
             f"- **{s['cases']} cases / {s['items']} pytest items**: " +
             ", ".join(f"{v} {k}" for k, v in sorted(s["outcomes"].items())),
             f"- Smoke subset: {s['smoke_items']} items · HTTP calls: {s['http_calls']} "
             f"(retried: {s['retried_calls']}) · routes: {s['routes']}",
             f"- Client-side latency: p50 {s['latency_ms'].get('p50')} ms, p95 {s['latency_ms'].get('p95')} ms",
             f"- Known defects: {', '.join(s['known_defects']) or 'none'}", "",
             "| Tier | passed | known defect | failed | skipped |", "|---|---|---|---|---|"]
    for t in TIERS:
        c = s["by_tier"][t]
        lines.append(f"| {t} | {c.get('passed', 0)} | {c.get('xfailed', 0)} | {c.get('failed', 0)} | "
                     f"{c.get('skipped', 0)} |")
    bad = [i for i in results["results"] if i["outcome"] not in ("passed",)]
    if bad:
        lines += ["", "## Not passed", "", "| Case | Item | Outcome | Detail |", "|---|---|---|---|"]
        lines += [f"| {i['case']} | `{i['nodeid'].split('::')[-1]}` | {i['outcome']} | {i['message']} |" for i in bad]
    return "\n".join(lines) + "\n"


def render_all(reports: Path, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    results, calls = load(reports)
    s = summary(results, calls)
    (reports / "summary.md").write_text(markdown(s, results))
    return [architecture(out), results_by_tier(results, out), coverage(results, calls, out),
            latency(calls, results["environment"], out), reports / "summary.md"]
