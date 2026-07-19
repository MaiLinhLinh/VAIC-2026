from app.config import get_settings

def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    get_settings.cache_clear()
    s = get_settings()
    assert s.llm_model == "gpt-oss-120b"
    assert s.enable_embeddings is False
    assert s.llm_base_url == "http://x/v1"
