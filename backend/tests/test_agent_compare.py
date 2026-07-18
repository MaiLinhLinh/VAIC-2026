from app.agent_core.compare import build_comparison


def _row(brand, price, dientnang):
    return {"model_code": brand, "brand": brand, "price_clean": price, "category": "Tủ Lạnh",
            "key_specs_summary": "", "full_specs_json":
            '{"Điện năng tiêu thụ": "%s kWh/năm"}' % dientnang}


def test_none_for_single():
    assert build_comparison([_row("A", 12_000_000, 350)], []) is None


def test_price_row_marks_cheapest_best():
    table = build_comparison([_row("A", 12_000_000, 350), _row("B", 11_000_000, 400)], [])
    price_row = next(r for r in table.rows if r.label == "Giá")
    assert price_row.cells[1].is_best is True   # B rẻ hơn
    assert price_row.cells[0].is_best is False
    assert len(table.products) == 2


def test_brand_row_present():
    table = build_comparison([_row("A", 12_000_000, 350), _row("B", 11_000_000, 400)], [])
    assert any(r.label == "Thương hiệu" for r in table.rows)


def test_energy_row_lower_is_best():
    table = build_comparison([_row("A", 12_000_000, 350), _row("B", 11_000_000, 400)],
                             ["tiết kiệm điện"])
    erow = next((r for r in table.rows if "Điện năng" in r.label), None)
    assert erow is not None
    assert erow.cells[0].is_best is True        # A tiêu thụ 350 < 400


def test_missing_cell_marked_unavailable():
    rows = [{"model_code": "A", "brand": "A", "price_clean": 0, "category": "X",
             "full_specs_json": "{}", "key_specs_summary": ""},
            {"model_code": "B", "brand": "B", "price_clean": 11_000_000, "category": "X",
             "full_specs_json": "{}", "key_specs_summary": ""}]
    table = build_comparison(rows, [])
    price_row = next(r for r in table.rows if r.label == "Giá")
    assert price_row.cells[0].available is False
    assert price_row.cells[0].value == "chưa có dữ liệu"
