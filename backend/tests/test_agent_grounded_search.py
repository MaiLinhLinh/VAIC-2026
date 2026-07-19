import sqlite3

from app.agent_core.advisor import generate_advisor
from app.agent_core.engine import AgentCoreEngine
from app.agent_core.agent_engine import (meta_inquiry_node, question_node, router_edge,
                                          _sanitize_required_features, _natural_followup)
from app.agent_core.retriever import retrieve_scored
from app.agent_core.slots import spec_slot_columns, update_slots
from app.agent_core.search_description import build_search_description
from app.llm.client import FakeLLM
from tests.agent_helpers import make_db


def _printer_db(tmp_path):
    db = str(tmp_path / "printers.db")
    make_db(db, [
        {"category": "Máy in", "category_table": "may_in", "model_code": "INK-1",
         "brand": "Canon", "price_clean": 3_000_000,
         "specs": {"Loại sản phẩm": "In phun màu", "Kết nối": "USB", "Tốc độ in": "10 trang/phút"}},
        {"category": "Máy in", "category_table": "may_in", "model_code": "LASER-1",
         "brand": "HP", "price_clean": 5_000_000,
         "specs": {"Loại sản phẩm": "In laser trắng đen", "Kết nối": "Wi-Fi", "Tốc độ in": "20 trang/phút"}},
    ])
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE may_in ("Loại sản phẩm" TEXT, "Kết nối" TEXT, "Tốc độ in" TEXT, "Ghi chú hiếm" TEXT)')
    conn.executemany('INSERT INTO may_in VALUES (?, ?, ?, ?)', [
        ("In phun màu", "USB", "10 trang/phút", None),
        ("In laser trắng đen", "Wi-Fi", "20 trang/phút", "chỉ một dòng"),
        ("In laser màu", "LAN", "18 trang/phút", None),
        ("In phun màu", "USB", "12 trang/phút", None),
        ("In phun màu", "Wi-Fi", "15 trang/phút", None),
    ])
    conn.commit()
    conn.close()
    return db


def test_hard_product_type_never_falls_back_to_wrong_type(tmp_path):
    db = _printer_db(tmp_path)
    result = retrieve_scored(
        "Máy in", None, [("Loại sản phẩm", "laser màu")], [], db_path=db,
        hard_slots=[("Loại sản phẩm", "laser màu")],
    )
    assert result["status"] == "no_products_found"
    assert result["top_3_products"] == []


def test_hard_product_type_only_returns_rows_with_evidence(tmp_path):
    db = _printer_db(tmp_path)
    result = retrieve_scored(
        "Máy in", None, [("Loại sản phẩm", "laser")], [], db_path=db,
        hard_slots=[("Loại sản phẩm", "laser")],
    )
    assert [p["model_code"] for p in result["top_3_products"]] == ["LASER-1"]


def test_required_term_is_checked_against_search_description(tmp_path):
    db = _printer_db(tmp_path)
    result = retrieve_scored(
        "Máy in", None, [], [], db_path=db, required_terms=["laser màu"]
    )
    assert result["status"] == "relaxed_preferences"
    assert result["top_3_products"]
    assert result["relaxed_features"] == ["laser màu"]
    assert "search_description" in result["sql_query"]
    assert "laser màu" in result["sql_params"]


def test_relaxing_features_never_exceeds_budget(tmp_path):
    result = retrieve_scored(
        "Máy in", 4_000_000, [], [], db_path=_printer_db(tmp_path),
        required_terms=["tính năng không tồn tại"],
    )

    assert result["status"] == "relaxed_preferences"
    assert [p["model_code"] for p in result["top_3_products"]] == ["INK-1"]
    assert all(float(p["price_clean"]) <= 4_000_000 for p in result["top_3_products"])
    assert "price_clean <= 4000000" in result["sql_display"]


def test_inferred_required_features_are_demoted_but_stated_type_is_kept():
    inferred = _sanitize_required_features(
        {"required_features": ["call", "gọi điện", "tin nhắn"],
         "priority_features": ["SOS"]},
        "mua đồng hồ cho người già",
    )
    stated = _sanitize_required_features(
        {"required_features": ["laser"], "priority_features": []},
        "tôi cần máy in laser",
    )

    assert inferred["required_features"] == []
    assert inferred["priority_features"] == ["SOS", "call", "gọi điện", "tin nhắn"]
    assert stated["required_features"] == ["laser"]


def test_soft_description_terms_are_used_by_sql_for_ranking(tmp_path):
    db = _printer_db(tmp_path)
    result = retrieve_scored("Máy in", None, [], ["Wi-Fi"], db_path=db)

    assert "search_description" in result["sql_query"]
    assert "ORDER BY" in result["sql_query"]
    assert result["sql_params"][-1] == "Wi-Fi"
    assert "DESCRIPTION_SCORE" in result["sql_display"]
    assert "'Wi-Fi'" in result["sql_display"]
    assert result["top_3_products"][0]["model_code"] == "LASER-1"


def test_criterion_advice_question_does_not_trigger_top_products():
    state = {
        "query": "băng tần nào thì tốt cho người già?",
        "intent": {
            "category": "Loa",
            "is_meta_inquiry": False,
            "is_chitchat": False,
            "wants_comparison": False,
            "declines_more_info": False,
        },
        "slots": [],
        "clarify_count": 3,
        "offered_clarify_count": -1,
        "slot_stage": None,
        "next_question": "Ngân sách dự kiến của anh/chị khoảng bao nhiêu ạ?",
        "history": [],
    }

    assert router_edge(state) == "question"


def test_criterion_options_are_answered_from_db_without_triggering_top(tmp_path):
    db = _printer_db(tmp_path)
    state = {
        "query": "có các loại sản phẩm nào nhỉ?",
        "intent": {"category": "Máy in", "is_meta_inquiry": False,
                   "is_chitchat": False, "wants_comparison": False,
                   "declines_more_info": False},
        "slots": [], "clarify_count": 3, "offered_clarify_count": -1,
        "slot_stage": None,
        "next_question": "Về loại sản phẩm, anh/chị có yêu cầu cụ thể nào không ạ?",
        "history": [],
    }

    assert router_edge(state) == "question"
    result = question_node(state, {"configurable": {"llm": FakeLLM(), "db_path": db}})

    assert result["stage"] == "collecting"
    assert result["clarify_count"] == 3
    assert "In phun màu" in result["response"]
    assert "In laser trắng đen" in result["response"]
    assert "top mấy" not in result["response"]


def test_search_description_excludes_commercial_and_id_fields():
    text = build_search_description("Máy in", "HP", {
        "Loại sản phẩm": "In laser", "Kết nối": "Wi-Fi", "giá gốc": "5.000.000đ",
        "khuyến mãi quà": "Tặng giấy", "productidweb": "12345",
    })
    assert "In laser" in text and "Wi-Fi" in text and "Nhãn hàng: HP" in text
    assert "5.000.000" not in text and "Tặng giấy" not in text and "12345" not in text


def test_questions_only_use_sufficiently_populated_db_columns(tmp_path):
    cols = spec_slot_columns("may_in", _printer_db(tmp_path))
    assert "Loại sản phẩm" in cols
    assert "Kết nối" in cols
    assert "Ghi chú hiếm" not in cols


def test_slot_question_is_built_from_valid_db_slots_only():
    llm = FakeLLM(json_responses=[{
        "slots": [{"name": "Loại sản phẩm", "value": "laser", "status": "filled",
                   "basis": "stated", "hard": True}],
        "next_slots": ["Tuổi người mua"],
        "next_question": "Người mua bao nhiêu tuổi?",
    }])
    result = update_slots(llm, "máy in laser", [], "Máy in",
                          ["Loại sản phẩm", "Kết nối"], [])
    assert result["slots"][0]["hard"] is True
    assert result["next_slots"] == ["Kết nối"]
    assert result["next_question"] == "Về kết nối, anh/chị có yêu cầu cụ thể nào không ạ?"


def test_question_order_is_usage_then_budget_then_db_field(tmp_path):
    db = _printer_db(tmp_path)
    intent1 = {"category": "Máy in", "budget_max": None, "priority_features": ["laser"],
               "required_features": ["laser"],
               "needs_clarification": True, "is_meta_inquiry": False,
               "clarification_questions": [], "brand": None}
    intent2 = {"category": "Máy in", "budget_max": None, "priority_features": ["văn phòng"],
               "needs_clarification": True, "is_meta_inquiry": False,
               "clarification_questions": [], "brand": None}
    intent3 = {"category": "Máy in", "budget_max": 5_000_000, "priority_features": [],
               "needs_clarification": False, "is_meta_inquiry": False,
               "clarification_questions": [], "brand": None}
    intent4 = {"category": "Máy in", "budget_max": None, "priority_features": ["Wi-Fi"],
               "needs_clarification": False, "is_meta_inquiry": False,
               "clarification_questions": [], "brand": None,
               "slot_updates": [
        {"name": "Loại sản phẩm", "value": "laser", "status": "filled",
         "basis": "stated", "hard": True},
        {"name": "Kết nối", "value": "Wi-Fi", "status": "filled",
         "basis": "stated", "hard": False},
        {"name": "Tốc độ in", "value": None, "status": "dontcare",
         "basis": "stated", "hard": False},
    ]}
    llm = FakeLLM(json_responses=[intent1, intent2, intent3, intent4])
    eng = AgentCoreEngine(llm=llm, db_path=db)

    first = eng.handle("printer", "tìm máy in laser")
    second = eng.handle("printer", "dùng cho văn phòng")
    third = eng.handle("printer", "khoảng 5 triệu")
    assert len(llm.calls) == 3  # không còn LLM call thứ hai chỉ để chọn câu hỏi
    fourth = eng.handle("printer", "cần loại laser, có Wi-Fi; tốc độ nào cũng được")
    assert len(llm.calls) == 4  # intent + slot + câu hỏi tiếp theo dùng chung một call mỗi lượt

    assert "cho ai" in first["reply"] and "dùng để làm gì" in first["reply"]
    assert "Ngân sách" in second["reply"]
    assert third["question"] == (
        "Về loại sản phẩm, kết nối và tốc độ in, anh/chị có yêu cầu cụ thể nào không ạ?"
    )
    assert "top mấy sản phẩm" in fourth["reply"]


def test_natural_discovery_question_reuses_intent_call_without_generic_wrapper(tmp_path):
    question = (
        "Dạ, máy in laser là lựa chọn hợp lý nếu mình in văn bản thường xuyên. "
        "Anh mua máy chủ yếu cho gia đình hay văn phòng ạ?"
    )
    llm = FakeLLM(json_responses=[{
        "category": "Máy in", "budget_max": None,
        "priority_features": ["laser"], "required_features": ["laser"],
        "needs_clarification": True, "is_meta_inquiry": False,
        "has_usage_context": False,
        "followup_focus": "usage", "followup_fields": [],
        "followup_question": question,
    }])

    result = AgentCoreEngine(llm=llm, db_path=_printer_db(tmp_path)).handle(
        "natural-question", "tôi muốn tìm máy in laser"
    )

    assert result["reply"] == question
    assert result["question"] == question
    assert len(llm.calls) == 1


def test_natural_spec_question_must_reference_only_allowed_db_fields():
    intent = {
        "followup_focus": "specs",
        "followup_fields": ["Độ tuổi"],
        "followup_question": "Mình muốn chọn theo độ tuổi nào ạ?",
    }
    assert _natural_followup(intent, "specs", ["Loại sản phẩm", "Kết nối"]) is None

    intent["followup_fields"] = ["Kết nối"]
    intent["followup_question"] = "Mình ưu tiên Wi-Fi hay chỉ cần cắm dây USB ạ?"
    assert _natural_followup(intent, "specs", ["Loại sản phẩm", "Kết nối"]) == intent["followup_question"]


def test_unknown_budget_continues_to_description_fields(tmp_path):
    db = _printer_db(tmp_path)
    intents = [
        {"category": "Máy in", "budget_max": None, "priority_features": [],
         "required_features": [], "needs_clarification": True, "is_meta_inquiry": False,
         "clarification_questions": [], "brand": None, "declines_more_info": False},
        {"category": None, "budget_max": None, "priority_features": [],
         "required_features": [], "needs_clarification": False, "is_meta_inquiry": False,
         "clarification_questions": [], "brand": None, "declines_more_info": False},
        # Mô phỏng model hiểu sai; deterministic guard phải sửa lại thành False.
        {"category": None, "budget_max": None, "priority_features": [],
         "required_features": [], "needs_clarification": False, "is_meta_inquiry": False,
         "clarification_questions": [], "brand": None, "declines_more_info": True},
    ]
    eng = AgentCoreEngine(llm=FakeLLM(json_responses=intents), db_path=db)

    eng.handle("unknown-budget", "tôi muốn mua máy in")
    eng.handle("unknown-budget", "mua cho bố dùng tại nhà")
    result = eng.handle("unknown-budget", "không biết")

    assert result["stage"] == "collecting"
    assert result["need"]["category"] == "Máy in"
    assert result["trace"][0]["data"]["declines_more_info"] is False
    assert "loại sản phẩm" in result["reply"].lower()
    assert result["recommendation"] is None


def test_no_result_reply_is_deterministic_and_does_not_call_llm():
    llm = FakeLLM(text_responses=["Một sản phẩm bịa"])
    message, streamed, warnings = generate_advisor(
        "máy in laser màu", {"category": "Máy in"}, [], "no_products_found", llm, []
    )
    assert "không tìm thấy sản phẩm nào khớp đầy đủ" in message
    assert llm.calls == []
    assert streamed is False
    assert warnings == []


def test_answering_customer_question_does_not_count_as_clarification():
    state = {"intent": {"meta_reply": "Dạ, laser dùng mực bột ạ."},
             "history": [], "clarify_count": 2}
    result = meta_inquiry_node(state, {})
    assert result["clarify_count"] == 2


def test_concept_question_wins_over_compare_threshold_and_resumes_clarification():
    state = {
        "query": "tải trọng máy là gì?",
        "intent": {
            "category": "Máy giặt",
            "is_meta_inquiry": False,  # simulate an imperfect LLM classification
            "is_chitchat": False,
            "wants_comparison": False,
            "declines_more_info": False,
        },
        "slots": [],
        "clarify_count": 3,
        "offered_clarify_count": -1,
        "offered_touched": -1,
        "slot_stage": None,
        "next_question": "Ngân sách dự kiến của anh/chị khoảng bao nhiêu ạ?",
        "history": [],
    }

    assert router_edge(state) == "question"

    llm = FakeLLM(text_responses=["Câu trả lời không được phép dùng."])
    result = question_node(state, {"configurable": {"llm": llm}})

    assert result["stage"] == "collecting"
    assert "quần áo khô" in result["response"]
    assert result["question"] == state["next_question"]
    assert state["next_question"] in result["response"]
    assert result["clarify_count"] == 3
    assert result["cards"] == []
    assert llm.calls == []
