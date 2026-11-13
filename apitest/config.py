"""Environment selection: one suite, many targets (in-process, local, docker, staging)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "environments.yaml"
_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    issuer: str
    audience: str
    secret: str = field(repr=False)


@dataclass(frozen=True)
class Environment:
    name: str
    mode: str                 # "inproc" | "http"
    base_url: str
    test_hooks: bool
    timeout_s: float
    retries: int
    auth: AuthSettings
    tenants: tuple[str, ...]
    routes: tuple[tuple[str, str], ...]


def _expand(value: str, env: dict) -> str:
    def sub(m):
        if m.group(1) not in env:
            raise ConfigError(f"environment variable {m.group(1)} is not set")
        return env[m.group(1)]
    return _VAR.sub(sub, value)


def load_environment(name: str | None = None, path: str | Path | None = None, env: dict | None = None) -> Environment:
    env = dict(os.environ if env is None else env)
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text())
    name = name or env.get("API_ENV") or "inproc"
    if name not in raw["environments"]:
        raise ConfigError(f"unknown environment {name!r}; choose from {', '.join(raw['environments'])}")
    e = {**raw.get("defaults", {}), **raw["environments"][name]}
    if e["mode"] not in ("inproc", "http"):
        raise ConfigError(f"{name}: mode must be inproc or http")
    a = raw["auth"]
    secret = env.get(a["secret_env"])
    if not secret:
        if not e.get("test_hooks", False):
            raise ConfigError(f"{name}: {a['secret_env']} must be set (dev secret is only for local targets)")
        secret = a["dev_secret"]
    return Environment(
        name=name, mode=e["mode"], base_url=_expand(e["base_url"], env).rstrip("/"),
        test_hooks=bool(e.get("test_hooks", False)), timeout_s=float(e.get("timeout_s", 5)),
        retries=int(e.get("retries", 0)),
        auth=AuthSettings(issuer=a["issuer"], audience=a["audience"], secret=secret),
        tenants=tuple(raw.get("tenants", ())), routes=tuple(tuple(r) for r in raw.get("routes", ())),
    )
