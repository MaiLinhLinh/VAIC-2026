from app.llm.client import FakeLLM, DeepSeekClient


def test_fake_llm_returns_queued():
    fake = FakeLLM(json_responses=[{"category": "tu_lanh"}], text_responses=["Chào anh"])
    assert fake.complete_json("s", "u") == {"category": "tu_lanh"}
    assert fake.complete_text("s", "u") == "Chào anh"


def test_extract_json_handles_fences():
    raw = "```json\n{\"a\": 1}\n```"
    assert DeepSeekClient._extract_json(raw) == {"a": 1}
    assert DeepSeekClient._extract_json('{"b": 2}') == {"b": 2}
