from app.catalog.category_config import CATEGORY_CONFIGS, config_for, SHEET_TO_CODE


def test_all_six_categories_present():
    assert set(CATEGORY_CONFIGS) == {
        "tu_lanh", "may_say", "may_rua_chen", "tu_mat", "dong_ho", "man_hinh"}


def test_sheet_mapping():
    assert SHEET_TO_CODE["Tủ Lạnh"] == "tu_lanh"
    assert SHEET_TO_CODE["Đồng hồ thông minh"] == "dong_ho"


def test_tu_lanh_has_critical_slots_and_prefs():
    cfg = config_for("tu_lanh")
    assert cfg.display == "Tủ lạnh"
    # có ít nhất 1 slot critical (importance 3)
    assert any(s.importance == 3 for s in cfg.ask_slots)
    # ưu tiên "tiết kiệm điện" phải map tới field điện năng, direction min
    sigs = cfg.pref_lexicon["tiết kiệm điện"]
    assert any(sig.field == "Điện năng tiêu thụ" and sig.direction == "min" for sig in sigs)


def test_pref_signal_fields_exist_in_specs():
    # mọi field trong lexicon & exclusion phải là spec đã khai báo (tránh lệch tên)
    for cfg in CATEGORY_CONFIGS.values():
        spec_fields = {s.field for s in cfg.specs}
        for sigs in cfg.pref_lexicon.values():
            for sig in sigs:
                assert sig.field in spec_fields, f"{cfg.code}: {sig.field} không có trong specs"
        for rule in cfg.exclusion_rules:
            assert rule.field in spec_fields
