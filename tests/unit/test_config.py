import pytest

from apitest.config import ConfigError, load_environment


def test_default_is_inproc():
    env = load_environment(env={})
    assert env.name == "inproc" and env.mode == "inproc" and env.test_hooks
    assert env.auth.secret == "dev-only-secret-change-me-0123456789"
    assert env.tenants == ("acme", "globex")


def test_api_env_variable_selects_target():
    env = load_environment(env={"API_ENV": "docker"})
    assert env.base_url == "http://api:8000" and env.mode == "http"


def test_explicit_name_wins_over_variable():
    assert load_environment("local", env={"API_ENV": "docker"}).name == "local"


def test_staging_needs_url_and_secret():
    with pytest.raises(ConfigError, match="STAGING_API_URL"):
        load_environment("staging", env={"API_JWT_SECRET": "s" * 32})
    with pytest.raises(ConfigError, match="API_JWT_SECRET"):
        load_environment("staging", env={"STAGING_API_URL": "https://api.staging.example.test"})
    env = load_environment("staging", env={"STAGING_API_URL": "https://api.staging.example.test/",
                                           "API_JWT_SECRET": "s" * 32})
    assert env.base_url == "https://api.staging.example.test"
    assert not env.test_hooks and env.retries == 2 and env.timeout_s == 15
    assert "s" * 32 not in repr(env)          # the secret never shows up in logs or reprs


def test_unknown_environment():
    with pytest.raises(ConfigError, match="unknown environment"):
        load_environment("prod-eu", env={})
