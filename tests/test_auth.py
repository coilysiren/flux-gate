import pytest

from flux_gate import ActorsConfig, ApiKeyAuth, BearerAuth, to_actor_headers


def test_bearer_auth_reads_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALICE_TOKEN", "secret-token-for-alice")

    config = ActorsConfig(actors={"alice": BearerAuth(type="bearer", token_env="ALICE_TOKEN")})
    headers = to_actor_headers(config)

    assert headers == {"alice": {"Authorization": "Bearer secret-token-for-alice"}}


def test_api_key_auth_reads_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOB_KEY", "bob-api-secret")

    config = ActorsConfig(
        actors={"bob": ApiKeyAuth(type="api_key", header="X-API-Key", key_env="BOB_KEY")}
    )
    headers = to_actor_headers(config)

    assert headers == {"bob": {"X-API-Key": "bob-api-secret"}}


def test_mixed_actors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALICE_TOKEN", "tok-a")
    monkeypatch.setenv("BOB_KEY", "key-b")

    config = ActorsConfig(
        actors={
            "alice": BearerAuth(type="bearer", token_env="ALICE_TOKEN"),
            "bob": ApiKeyAuth(type="api_key", header="X-Custom-Auth", key_env="BOB_KEY"),
        }
    )
    headers = to_actor_headers(config)

    assert headers["alice"] == {"Authorization": "Bearer tok-a"}
    assert headers["bob"] == {"X-Custom-Auth": "key-b"}


def test_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    config = ActorsConfig(actors={"alice": BearerAuth(type="bearer", token_env="MISSING_TOKEN")})

    with pytest.raises(ValueError, match="MISSING_TOKEN"):
        to_actor_headers(config)
