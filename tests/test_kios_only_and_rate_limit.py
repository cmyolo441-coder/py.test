import pytest

from fullagent import client, config
from fullagent.config import Model, Provider


def test_configured_providers():
    assert list(config.PROVIDERS) == ["kios", "gwarden", "nvidia"]
    assert {m.id for m in config.MODELS if m.provider == "kios"} == {
        "mistral-large-4", "ernie-5.5-flash-preview",
        "deepseek-v4.1-flash-free"}
    assert {m.id for m in config.MODELS} == {
        "deepseek-v4.1-flash-1017", "gemini-4-argon",
        "mistral-large-4", "ernie-5.5-flash-preview",
        "deepseek-v4.1-flash-free", "mistralai/mistral-large-4-1t-a52b",
        "moonshotai/kimi-k3.1-pro"}
    assert config.DEFAULT_MODEL_ID == "deepseek-v4.1-flash-1017"
    gwarden = config.PROVIDERS["gwarden"]
    assert gwarden.base_url == "https://gwarden.su/v1"
    assert gwarden.api_key, "gwarden must have embedded zero-config key"


def test_kios_ships_embedded_api_key():
    provider = config.PROVIDERS["kios"]
    assert provider.api_key, "kios must have embedded zero-config key"
    headers = client._base_headers(provider, stream=False)
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["Content-Type"] == "application/json"


def test_keyless_provider_still_requires_credentials():
    with pytest.raises(client.APIError, match="no API key configured"):
        client._check_api_key(
            Provider(key="kios", name="Kios API",
                     base_url="https://kiosapi.com/v1", api_key="",
                     color="#8be9fd"),
            Model("deepseek-v4.1-flash-free", "kios",
                  "DeepSeek V4.1 Flash Free"),
        )


def test_dead_model_error_is_not_retried(monkeypatch):
    """A retired model id must fail immediately, not burn 2+4+8s of backoff.

    Kios answers unknown/retired model ids with 503 model_not_found. That
    is permanent: retrying only made the UI feel frozen for ~14s."""
    attempts = []

    def fake_once(*_args, **_kwargs):
        attempts.append(True)
        raise client.APIError(
            "model_not_found: No available channel for model", status=503)

    monkeypatch.setattr(client, "_chat_stream_once", fake_once)
    with pytest.raises(client.APIError, match="model_not_found"):
        client._chat_stream_with_retries(
            "https://example.test", {}, {}, None, None, None, None, None, 5,
        )
    assert len(attempts) == 1


def test_retry_after_parses_seconds_and_ignores_invalid_values():
    assert client._parse_retry_after("3.5") == 3.5
    assert client._parse_retry_after("not a delay") is None
    assert client._parse_retry_after(None) is None


def test_streaming_429_retries_until_success(monkeypatch):
    attempts = []
    statuses = []
    expected = object()

    def fake_once(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) < 3:
            raise client.APIError("rate limited", status=429, retry_after=0)
        return expected

    monkeypatch.setattr(client, "_chat_stream_once", fake_once)
    result = client._chat_stream_with_retries(
        "https://example.test", {}, {}, None, None, None, None, None, 1,
        on_status=statuses.append,
    )

    assert result is expected
    assert len(attempts) == 3
    assert len(statuses) == 2
    assert all("429" in status for status in statuses)


def test_blocking_429_retries_and_closes_each_response(monkeypatch):
    class Response:
        def __init__(self, status, headers=None, body="rate limited", data=None):
            self.status_code = status
            self.headers = headers or {}
            self.text = body
            self._data = data
            self.closed = False

        def json(self):
            return self._data

        def close(self):
            self.closed = True

    first = Response(429, {"Retry-After": "0"})
    second = Response(200, data={"ok": True})

    class Session:
        def __init__(self):
            self.responses = [first, second]
            self.calls = 0

        def post(self, *_args, **_kwargs):
            self.calls += 1
            return self.responses.pop(0)

    session = Session()
    waits = []
    monkeypatch.setattr(client, "_http", lambda: session)
    monkeypatch.setattr(client, "sleep_cancellable",
                        lambda delay, _cancel: waits.append(delay))

    result = client._post_blocking(
        "https://example.test", {}, {}, timeout=1)

    assert result == {"ok": True}
    assert session.calls == 2
    assert first.closed and second.closed
    assert waits == [0.0]


def test_nvidia_provider_reads_key_from_environment_only(monkeypatch, tmp_path):
    nvidia = config.PROVIDERS["nvidia"]
    assert nvidia.base_url == "https://integrate.api.nvidia.com/v1"
    assert "nvidia" not in config._EMBEDDED_KEYS, "no NVIDIA key may ship"
    assert [m.id for m in config.MODELS if m.provider == "nvidia"] == [
        "mistralai/mistral-large-4-1t-a52b", "moonshotai/kimi-k3.1-pro"]
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert config._provider_api_key("nvidia") == ""
    monkeypatch.setenv("NVIDIA_API_KEY", "  test-value  ")
    assert config._provider_api_key("nvidia") == "test-value"
    monkeypatch.delenv("NVIDIA_API_KEY")
    (tmp_path / "nvidia_api_key").write_text("from-file\n")
    assert config._provider_api_key("nvidia") == "from-file"


def test_nvidia_without_key_gives_clear_error():
    keyless = Provider(key="nvidia", name="NVIDIA API",
                       base_url="https://integrate.api.nvidia.com/v1",
                       api_key="", color="#76b900")
    with pytest.raises(client.APIError, match="NVIDIA_API_KEY"):
        client._check_api_key(keyless)


def test_no_nvidia_key_in_source_tree():
    import pathlib, re
    root = pathlib.Path(config.__file__).resolve().parent.parent
    pat = re.compile(r"nvapi-[A-Za-z0-9_-]{20,}")
    for path in list(root.glob("fullagent/**/*.py")) + [root / "main.py"]:
        assert not pat.search(path.read_text(errors="ignore")), path
