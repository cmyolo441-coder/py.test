import pytest

from fullagent import client, config
from fullagent.config import Model, Provider


def test_kios_is_the_only_configured_provider():
    assert list(config.PROVIDERS) == ["kios"]
    assert {m.id for m in config.MODELS} == {
        "atria-dawn-preview", "step-5-preview-free",
        "ling-3.1-flash", "glyph-cluster"}
    assert all(m.provider == "kios" for m in config.MODELS)
    assert config.DEFAULT_MODEL_ID == "step-5-preview-free"


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
            Model("step-5-preview-free", "kios", "Step 5 Preview Free"),
        )


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
