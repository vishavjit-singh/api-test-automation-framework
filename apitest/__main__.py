"""``python -m apitest <command>``: reports and the optional Xray upload."""
from __future__ import annotations

import json
import os
from pathlib import Path

import click


@click.group()
def cli():
    """apitest utilities."""


@cli.command()
@click.option("--reports", "reports_dir", default="reports", type=click.Path(file_okay=False))
@click.option("--out", default="docs/images", type=click.Path(file_okay=False))
def report(reports_dir, out):
    """Charts and summary.md from the last run."""
    from .report import render_all, summary, load

    for p in render_all(Path(reports_dir), Path(out)):
        click.echo(f"wrote {p}")
    click.echo(json.dumps(summary(*load(Path(reports_dir))), indent=2))


@cli.command("xray-upload")
@click.option("--file", "path", default="reports/xray-results.json", type=click.Path(exists=True, dir_okay=False))
@click.option("--send", is_flag=True, help="Actually upload (needs XRAY_CLIENT_ID / XRAY_CLIENT_SECRET).")
def xray_upload(path, send):
    """Import the execution into Xray Cloud. Without --send, validate and print what would be sent."""
    from .xray import upload

    execution = json.loads(Path(path).read_text())
    statuses = {}
    for t in execution["tests"]:
        statuses[t["status"]] = statuses.get(t["status"], 0) + 1
    click.echo(f"{len(execution['tests'])} test results for {execution['info'].get('testPlanKey', '-')}: {statuses}")
    if not send:
        click.echo("dry run: nothing sent (use --send)")
        return
    cid, secret = os.environ.get("XRAY_CLIENT_ID"), os.environ.get("XRAY_CLIENT_SECRET")
    if not (cid and secret):
        raise click.ClickException("XRAY_CLIENT_ID and XRAY_CLIENT_SECRET must be set")
    click.echo(json.dumps(upload(execution, cid, secret)))


if __name__ == "__main__":
    cli()
