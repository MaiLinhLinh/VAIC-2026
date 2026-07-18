"""Bộ thẩm định của tool SQL: chỉ SELECT một câu, bảng whitelist, ép LIMIT, chạy chỉ-đọc."""
import sqlite3

import pytest

from app.agent_core.sql_tool import validate_sql, run_sql_readonly


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "mini.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE all_products (model_code TEXT, price_clean REAL)")
    conn.execute("CREATE TABLE tu_lanh (model_code TEXT, \"Dung tích tổng\" TEXT)")
    conn.execute("INSERT INTO all_products VALUES ('M1', 5000000)")
    conn.execute("INSERT INTO tu_lanh VALUES ('M1', '313 lít')")
    conn.commit()
    conn.close()
    return path


def test_chi_cho_select(db):
    _, reject = validate_sql("DELETE FROM all_products", db)
    assert reject is not None


def test_chan_hai_cau_lenh(db):
    _, reject = validate_sql("SELECT * FROM all_products; DROP TABLE all_products", db)
    assert reject is not None


def test_chan_tu_khoa_cam_trong_cau_select(db):
    _, reject = validate_sql("SELECT * FROM all_products WHERE 1=1 UNION SELECT * FROM pragma_table_info('x')", db)
    assert reject is not None


def test_chan_bang_ngoai_whitelist(db):
    _, reject = validate_sql("SELECT * FROM sqlite_master", db)
    assert reject == "bảng không được phép: sqlite_master"


def test_ep_limit_khi_thieu(db):
    sql, reject = validate_sql("SELECT * FROM all_products", db)
    assert reject is None
    assert sql.endswith("LIMIT 20")


def test_kep_limit_qua_lon(db):
    sql, reject = validate_sql("SELECT * FROM all_products LIMIT 9999", db)
    assert reject is None
    assert "LIMIT 20" in sql


def test_chay_chi_doc_va_cast_don_vi(db):
    rows = run_sql_readonly(
        'SELECT model_code FROM tu_lanh WHERE CAST("Dung tích tổng" AS REAL) >= 300', db)
    assert [r["model_code"] for r in rows] == ["M1"]


def test_ket_noi_chi_doc_khong_ghi_duoc(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO all_products VALUES ('X', 1)")
    conn.close()
