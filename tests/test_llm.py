import pytest

from jobscout.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, get_llm


def test_get_llm_uses_default_model_base_url_and_deny_routing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    llm = get_llm()

    assert llm.model_name == DEFAULT_MODEL
    assert llm.openai_api_base == DEFAULT_BASE_URL
    assert llm.extra_body == {"provider": {"data_collection": "deny"}}
    assert llm.openai_api_key.get_secret_value() == "test-key"


def test_get_llm_model_env_var_switches_model_with_no_code_change(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")

    llm = get_llm()

    assert llm.model_name == "anthropic/claude-haiku-4.5"


def test_get_llm_model_arg_overrides_env_var(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")

    llm = get_llm(model="qwen/qwen3.8-flash")

    assert llm.model_name == "qwen/qwen3.8-flash"


def test_get_llm_base_url_env_var_override(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test/v1")

    llm = get_llm()

    assert llm.openai_api_base == "https://example.test/v1"


def test_get_llm_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_llm()


def test_get_llm_empty_env_var_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "")

    llm = get_llm()

    assert llm.model_name == DEFAULT_MODEL
    assert llm.openai_api_base == DEFAULT_BASE_URL
