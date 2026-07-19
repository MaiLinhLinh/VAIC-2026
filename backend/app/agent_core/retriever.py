import sqlite3
import re
import math
import json
from typing import List, Dict, Any, Optional, Tuple
from app.config import get_settings
from app.nlu.preprocess import strip_accents
from app.agent_core.search_description import build_search_description


def _resolve_db(db_path: Optional[str]) -> str:
    return db_path or get_settings().agent_db_path


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_MIN_HINT = ("tren", "toi thieu", "lon", "cao", "tro len", "it nhat", ">=", ">", "manh", "trau", "lau", "ben")
_MAX_HINT = ("duoi", "toi da", "nho", "thap", "gon", "nhe", "mong", "<=", "<", "duoi muc")


def _leading_num(s: Any) -> Optional[float]:
    if s is None:
        return None
    m = _NUM_RE.search(str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def _match_slot(want_value: str, spec_value: str) -> float:
    """Mức khớp [0..1] giữa giá trị khách muốn và giá trị thực của sản phẩm cho một slot.
    Không loại bỏ ai — chỉ CHO ĐIỂM: khớp cao lên đầu, không có dữ liệu = 0 điểm slot đó."""
    fv = strip_accents(str(want_value).lower()).strip()
    sv_raw = str(spec_value).strip()
    fs = strip_accents(sv_raw.lower())
    if not fs or fs in ("nan", "none", "", "khong"):
        return 0.0
    nv, ns = _leading_num(want_value), _leading_num(spec_value)
    if nv is not None and ns is not None:
        if any(k in fv for k in _MIN_HINT):
            return 1.0 if ns >= nv else 0.25
        if any(k in fv for k in _MAX_HINT):
            return 1.0 if ns <= nv else 0.25
        rel = abs(ns - nv) / max(abs(nv), 1.0)
        return max(0.0, 1.0 - rel)  # càng sát càng cao
    # So khớp chữ: có token nào của nhu cầu xuất hiện trong giá trị sản phẩm.
    toks = [t for t in re.split(r"[^\wàáâãèéêìíòóôõùúýăđĩũơư]+", fv) if len(t) > 1]
    if toks and any(t in fs for t in toks):
        return 1.0
    return 0.0


def _score_row(description: str, specs: Dict[str, str], filled_slots: List[Tuple[str, str]],
               prefs: List[str], price: float) -> float:
    score = 0.0
    for name, val in filled_slots:
        sv = specs.get(name)
        if sv:
            score += _match_slot(val, sv)
    # Ưu tiên chữ tự do quét trên mô tả đã chọn lọc thay vì JSON thô nhiều nhiễu.
    if prefs:
        pool = strip_accents(description.lower())
        for p in prefs:
            fp = strip_accents(str(p).lower()).strip()
            tokens = [t for t in re.findall(r"\w+", fp) if len(t) > 1]
            if tokens:
                score += 0.6 * sum(1 for token in tokens if token in pool) / len(tokens)
    if price and price > 0:  # nhẹ nhàng ưu tiên máy có giá khả dụng
        score += 0.3
    return score


_GENERIC_VALUE_TOKENS = {"may", "in", "loai", "can", "muon", "co", "dung", "cho"}


def _hard_slot_matches(want_value: str, spec_value: Optional[str]) -> bool:
    """Ràng buộc cứng chỉ khớp khi chính giá trị của cột DB có bằng chứng."""
    if spec_value is None:
        return False
    fv = strip_accents(str(want_value).lower()).strip()
    fs = strip_accents(str(spec_value).lower()).strip()
    if not fs or fs in ("nan", "none", "null", "khong", "hang khong cong bo"):
        return False
    nv, ns = _leading_num(want_value), _leading_num(spec_value)
    if nv is not None and ns is not None:
        return _match_slot(want_value, spec_value) >= 0.9
    tokens = [t for t in re.split(r"[^\w]+", fv)
              if len(t) > 1 and t not in _GENERIC_VALUE_TOKENS]
    return bool(tokens) and all(re.search(rf"\b{re.escape(t)}\b", fs) for t in tokens)


def _hard_description_matches(term: str, description: str) -> bool:
    flat_term = strip_accents(str(term).lower())
    flat_description = strip_accents(str(description).lower())
    tokens = [t for t in re.findall(r"\w+", flat_term)
              if len(t) > 1 and t not in _GENERIC_VALUE_TOKENS]
    return bool(tokens) and all(
        re.search(rf"\b{re.escape(token)}\b", flat_description) for token in tokens
    )


def _description_tokens(terms: List[str]) -> List[str]:
    """Token tìm kiếm đã chuẩn hóa để dùng trực tiếp trong câu SQL mô tả."""
    tokens: List[str] = []
    for term in terms:
        flat = strip_accents(str(term).lower())
        for token in re.findall(r"\w+", flat):
            if len(token) > 1 and token not in _GENERIC_VALUE_TOKENS and token not in tokens:
                tokens.append(token)
    return tokens


def _description_sql_score(description: Any, term: Any) -> float:
    tokens = _description_tokens([str(term or "")])
    if not tokens:
        return 0.0
    pool = strip_accents(str(description or "").lower())
    return sum(1 for token in tokens if token in pool) / len(tokens)


def _description_order_sql(terms: List[str]) -> Tuple[str, List[str]]:
    clean_terms = list(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))
    if not clean_terms:
        return "", []
    score_parts = [
        "DESCRIPTION_SCORE(COALESCE(search_description, ''), ?)"
        for _ in clean_terms
    ]
    return " ORDER BY (" + " + ".join(score_parts) + ") DESC", clean_terms


def _sql_for_trace(sql: str, params: List[Any]) -> str:
    """Render bản SQL chỉ để truy vết; câu thực thi vẫn parameterized để an toàn."""
    rendered = sql
    for value in params:
        if value is None:
            literal = "NULL"
        elif isinstance(value, (int, float)):
            literal = str(value)
        else:
            literal = "'" + str(value).replace("'", "''") + "'"
        rendered = rendered.replace("?", literal, 1)
    return rendered


def retrieve_scored(category: Optional[str], budget_max: Optional[float],
                    filled_slots: List[Tuple[str, str]], prefs: Optional[List[str]],
                    top_n: int = 3, db_path: Optional[str] = None,
                    hard_slots: Optional[List[Tuple[str, str]]] = None,
                    brand: Optional[str] = None,
                    required_terms: Optional[List[str]] = None) -> Dict[str, Any]:
    """Lọc ngành/giá/hãng và điều kiện bắt buộc, rồi chấm điểm sở thích mềm.

    Điều kiện bắt buộc chỉ được đối chiếu với đúng cột spec tương ứng. Không có
    bằng chứng hoặc không có dòng khớp nghĩa là không có kết quả; không nới lỏng
    sang một loại sản phẩm khác.
    """
    db_path = _resolve_db(db_path)
    prefs = prefs or []
    hard_slots = hard_slots or []
    required_terms = required_terms or []
    required_tokens = _description_tokens(required_terms)
    relaxed_features: List[str] = []
    scoring_prefs = list(prefs)
    active_required_terms = list(required_terms)
    description_search = {
        "column": "search_description",
        "soft_terms": prefs,
        "required_terms": required_terms,
        "mode": "SQL lọc từ bắt buộc và ưu tiên từ mềm trên search_description; sau đó chấm điểm xếp hạng",
    }
    hard_filters = {"category": category, "budget_max": budget_max, "brand": brand}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.create_function(
        "NORMALIZE_TEXT", 1,
        lambda value: strip_accents(str(value or "").lower()),
        deterministic=True,
    )
    conn.create_function("DESCRIPTION_SCORE", 2, _description_sql_score, deterministic=True)
    base_conds, base_params = [], []
    if category and category.strip():
        base_conds.append("category = ?")
        base_params.append(category.strip())
    if budget_max and budget_max > 0:
        base_conds.append("price_clean > 0 AND price_clean <= ?")
        base_params.append(budget_max)
    if brand and brand.strip():
        base_conds.append("LOWER(brand) = LOWER(?)")
        base_params.append(brand.strip())
    conds, params = list(base_conds), list(base_params)
    for token in required_tokens:
        conds.append("NORMALIZE_TEXT(COALESCE(search_description, '')) LIKE ?")
        params.append(f"%{token}%")
    description_order, description_order_params = _description_order_sql(prefs)
    sql = (
        "SELECT * FROM all_products"
        + (" WHERE " + " AND ".join(conds) if conds else "")
        + description_order
    )
    sql_params = [*params, *description_order_params]
    rows = conn.execute(sql, sql_params).fetchall()
    sql_used, sql_params_used = sql, sql_params
    status = "scored_match"
    # Không có bằng chứng cho tính năng mô tả: giữ nguyên ngành/giá/hãng, hạ tính năng
    # xuống ưu tiên mềm và trả lựa chọn gần nhất với cảnh báo minh bạch.
    if not rows and required_terms:
        status = "relaxed_preferences"
        relaxed_features = list(required_terms)
        description_search["mode"] = "nới tính năng mô tả thành ưu tiên mềm; giữ nguyên category/price/brand"
        scoring_prefs = list(dict.fromkeys([*prefs, *required_terms]))
        active_required_terms = []
        relaxed_order, relaxed_order_params = _description_order_sql(scoring_prefs)
        relaxed_sql = (
            "SELECT * FROM all_products"
            + (" WHERE " + " AND ".join(base_conds) if base_conds else "")
            + relaxed_order
        )
        relaxed_params = [*base_params, *relaxed_order_params]
        rows = conn.execute(relaxed_sql, relaxed_params).fetchall()
        sql_used, sql_params_used = relaxed_sql, relaxed_params
    conn.close()
    if not rows:
        return {"status": "no_products_found", "sql_query": sql_used,
                "sql_display": _sql_for_trace(sql_used, sql_params_used),
                "sql_params": sql_params_used, "total_matches_found": 0,
                "top_3_products": [], "all_top_k": [],
                "description_search": description_search, "description_evidence": [],
                "relaxed_features": relaxed_features, "hard_filters": hard_filters}

    seen, cands = set(), []
    for r in rows:
        p = dict(r)
        code = str(p.get("model_code") or p.get("sku") or "")
        if code and code in seen:
            continue
        seen.add(code)
        try:
            specs = {str(k): str(v) for k, v in json.loads(p.get("full_specs_json") or "{}").items()}
        except (ValueError, TypeError):
            specs = {}
        if any(not _hard_slot_matches(value, specs.get(name)) for name, value in hard_slots):
            continue
        description = str(p.get("search_description") or "").strip()
        if not description:
            description = build_search_description(
                str(p.get("category") or ""), str(p.get("brand") or ""), specs
            )
        if any(not _hard_description_matches(term, description) for term in active_required_terms):
            continue
        price = float(p.get("price_clean") or 0)
        p["_score"] = _score_row(description, specs, filled_slots, scoring_prefs, price)
        p["name"] = (f"Model {p.get('model_code') or p.get('sku', 'N/A')} - {p.get('brand', '')}"
                     if p.get("model_code") or p.get("sku") else str(p.get("key_specs_summary", "Sản phẩm")))
        p["price"] = p.get("price_clean") or 0
        cands.append(p)
    if not cands:
        return {"status": "no_products_found", "sql_query": sql_used,
                "sql_display": _sql_for_trace(sql_used, sql_params_used),
                "sql_params": sql_params_used,
                "total_matches_found": 0, "top_3_products": [], "all_top_k": [],
                "description_search": description_search, "description_evidence": [],
                "relaxed_features": relaxed_features, "hard_filters": hard_filters}
    # Xếp: điểm cao trước; hoà điểm thì giá thấp trước (0/None coi như đắt nhất).
    cands.sort(key=lambda x: (-x["_score"], float(x.get("price_clean") or 1e18)))
    top = cands[:max(2, min(4, top_n))]
    description_evidence = []
    for row in top:
        description = str(row.get("search_description") or "")
        matched_terms = [term for term in [*required_terms, *prefs]
                         if _hard_description_matches(term, description)]
        description_evidence.append({
            "product": row.get("name"),
            "score": round(float(row.get("_score") or 0), 3),
            "matched_terms": matched_terms,
        })
    return {"status": status, "sql_query": sql_used,
            "sql_display": _sql_for_trace(sql_used, sql_params_used),
            "sql_params": sql_params_used,
            "total_matches_found": len(cands),
            "top_3_products": top, "all_top_k": cands[:5],
            "description_search": description_search,
            "description_evidence": description_evidence,
            "relaxed_features": relaxed_features, "hard_filters": hard_filters}


def get_catalog_metadata(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Dynamically fetches distinct categories, sample brands, and price ranges
    directly from the SQLite database without hardcoding.
    """
    db_path = _resolve_db(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get distinct categories
    cursor.execute("SELECT DISTINCT category FROM all_products WHERE category IS NOT NULL")
    categories = [row[0] for row in cursor.fetchall() if row[0]]
    
    # Get distinct brands
    cursor.execute("SELECT DISTINCT brand FROM all_products WHERE brand IS NOT NULL AND brand != 'Unknown'")
    brands = [row[0] for row in cursor.fetchall() if row[0]]
    
    conn.close()
    return {
        "categories": categories,
        "brands": brands
    }


def get_category_price_floor(category: str,
                             db_path: Optional[str] = None) -> Optional[float]:
    """Giá bán thấp nhất hợp lệ của một ngành trong catalog.

    Chỉ xét giá dương và khớp chính xác category, cùng quy ước lọc giá với các
    hàm retrieval. Trả ``None`` nếu ngành không tồn tại hoặc chưa có giá.
    """
    if not category or not str(category).strip():
        return None
    conn = sqlite3.connect(_resolve_db(db_path))
    try:
        row = conn.execute(
            "SELECT MIN(price_clean) FROM all_products "
            "WHERE category = ? AND price_clean IS NOT NULL AND price_clean > 0",
            (str(category).strip(),),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return float(row[0])


def catalog_field_values(category: str, query: str, db_path: Optional[str] = None,
                         max_fields: int = 3, max_values: int = 8) -> Dict[str, List[str]]:
    """Các giá trị thật của cột thông số được khách hỏi, dùng để giải đáp chen ngang."""
    table = category_table_for(category, db_path)
    if not table:
        return {}
    flat_query = strip_accents((query or "").lower())
    query_tokens = set(re.findall(r"\w+", flat_query))
    conn = sqlite3.connect(_resolve_db(db_path))
    try:
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        matched = []
        for column in columns:
            flat_column = strip_accents(str(column).lower())
            tokens = [token for token in re.findall(r"\w+", flat_column)
                      if len(token) > 2 and token not in _GENERIC_VALUE_TOKENS]
            if tokens and any(token in query_tokens for token in tokens):
                matched.append(column)
        out: Dict[str, List[str]] = {}
        for column in matched[:max_fields]:
            rows = conn.execute(
                f'SELECT TRIM(CAST("{column}" AS TEXT)) AS value, COUNT(*) AS n '
                f'FROM "{table}" WHERE "{column}" IS NOT NULL '
                f'AND TRIM(CAST("{column}" AS TEXT)) NOT IN ("", "nan", "None") '
                f'GROUP BY value ORDER BY n DESC, value ASC LIMIT ?',
                (max_values,),
            ).fetchall()
            values = [str(row[0]).strip() for row in rows if row[0]]
            if values:
                out[column] = values
        return out
    finally:
        conn.close()

def get_schema_summary(db_path: Optional[str] = None) -> str:
    """
    Returns a clean summary of the database schema for LangChain prompt injection.
    """
    db_path = _resolve_db(db_path)
    meta = get_catalog_metadata(db_path)
    cats_str = ", ".join(f"'{c}'" for c in meta["categories"])
    return f"Danh mục sản phẩm hiện có trong CSDL ({len(meta['categories'])} danh mục): [{cats_str}]"

def score_product(prod: Dict[str, Any], query: str, priority_features: Optional[List[str]] = None) -> float:
    """
    Dynamic relevance scoring combining query token overlap, priority feature matches, and spec richness.
    """
    score = 0.0
    name = str(prod.get("key_specs_summary", "") or prod.get("sku", "") or prod.get("model_code", ""))
    specs = str(prod.get("search_description") or prod.get("full_specs_json", ""))
    cat = str(prod.get("category", ""))
    brand = str(prod.get("brand", ""))
    
    text_pool = f"{name} {specs} {cat} {brand}".lower()
    query_lower = query.lower()
    
    # 1. Query token matching
    tokens = [t for t in re.findall(r'\w+', query_lower) if len(t) > 1]
    for token in tokens:
        if token in text_pool:
            score += 2.0
            if token in name.lower():
                score += 3.0
                
    # 2. Priority features matching
    if priority_features:
        for feat in priority_features:
            feat_lower = feat.lower()
            if feat_lower in text_pool:
                score += 5.0
            # Check capacity or numeric matches
            nums = re.findall(r'\d+(?:\.\d+)?', feat_lower)
            for n in nums:
                if n in name or n in specs:
                    score += 2.0
                    
    # 3. Prefer items with valid listed prices
    price_val = prod.get("price_clean")
    if not price_val or float(price_val) <= 0:
        score -= 3.0
    else:
        score += 2.0
        
    return score

def category_table_for(category: str, db_path: Optional[str] = None) -> Optional[str]:
    """Tên bảng thông số riêng của một danh mục (vd 'Tủ Lạnh' -> 'tu_lanh')."""
    conn = sqlite3.connect(_resolve_db(db_path))
    try:
        row = conn.execute("SELECT DISTINCT category_table FROM all_products WHERE category = ?",
                           (category,)).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def hydrate_rows(rows: List[Dict[str, Any]], db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Đổi kết quả tool SQL về dòng all_products chuẩn (có price_clean/full_specs_json cho
    fact card), giữ nguyên thứ tự. Nối bằng cột id (duy nhất) khi có; model_code KHÔNG duy
    nhất (biến thể chung mã) nên chỉ là đường lui khi truy từ bảng ngành."""
    ids = [r.get("id") for r in rows if r.get("id") is not None]
    codes = [str(r.get("model_code") or "").strip() for r in rows]
    conn = sqlite3.connect(_resolve_db(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if ids:
            marks = ",".join("?" * len(ids))
            got = {r["id"]: dict(r) for r in conn.execute(
                f"SELECT * FROM all_products WHERE id IN ({marks})", ids)}
            ordered = [got[i] for i in ids if i in got]
        else:
            codes = [c for c in codes if c]
            if not codes:
                return []
            marks = ",".join("?" * len(codes))
            got = {}
            for r in conn.execute(f"SELECT * FROM all_products WHERE model_code IN ({marks})", codes):
                got.setdefault(str(r["model_code"]), dict(r))
            seen: set = set()
            ordered = []
            for c in codes:
                if c in got and c not in seen:
                    ordered.append(got[c])
                    seen.add(c)
    finally:
        conn.close()
    out = []
    for p in ordered:
        p["_score"] = 0.0
        p["name"] = (f"Model {p.get('model_code') or p.get('sku', 'N/A')} - {p.get('brand', '')}"
                     if p.get("model_code") or p.get("sku")
                     else str(p.get("key_specs_summary", "Sản phẩm")))
        p["price"] = p.get("price_clean") or 0
        out.append(p)
    return out


def price_spread_products(category: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Khách từ chối chốt ngân sách -> chọn 3 đại diện rẻ / tầm trung / cao cấp của ngành
    (thay vì top theo điểm, để khách định hình mặt bằng giá)."""
    db_path = _resolve_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM all_products WHERE category = ? AND price_clean > 0 ORDER BY price_clean ASC"
    rows = conn.execute(sql, (category,)).fetchall()
    conn.close()
    prods = []
    for r in rows:
        p = dict(r)
        p["_score"] = 0.0
        p["name"] = (f"Model {p.get('model_code') or p.get('sku', 'N/A')} - {p.get('brand', '')}"
                     if p.get("model_code") or p.get("sku") else str(p.get("key_specs_summary", "Sản phẩm")))
        p["price"] = p.get("price_clean") or 0
        prods.append(p)
    if not prods:
        return {"status": "no_products_found", "sql_query": sql, "total_matches_found": 0,
                "top_3_products": [], "all_top_k": []}
    idx = sorted({0, len(prods) // 2, len(prods) - 1})
    picks = [prods[i] for i in idx]
    return {"status": "price_spread", "sql_query": sql.replace("?", f"'{category}'"),
            "total_matches_found": len(prods), "top_3_products": picks, "all_top_k": picks}


def search_products(
    query: str, 
    category: Optional[str] = None, 
    max_price: Optional[float] = None, 
    brand: Optional[str] = None,
    priority_features: Optional[List[str]] = None,
    top_k: int = 5,
    db_path: Optional[str] = None,
    is_meta_inquiry: bool = False
) -> Dict[str, Any]:
    """
    Hybrid retriever with exact status recognition ('exact_match', 'budget_fallback', 'no_products_found', 'meta_inquiry').
    If budget is too low and yields 0 results, dynamically retrieves the cheapest alternatives slightly above budget.
    """
    db_path = _resolve_db(db_path)
    query_lower = query.lower()
    if is_meta_inquiry or ((not category and not max_price and not brand and not priority_features) and any(w in query_lower for w in ["bao nhiêu", "loại", "danh mục", "dòng", "sản phẩm nào", "hiện có", "những gì"])):
        meta = get_catalog_metadata(db_path)
        return {
            "status": "meta_inquiry",
            "sql_query": "SELECT DISTINCT category FROM all_products",
            "total_matches_found": len(meta["categories"]),
            "top_3_products": [],
            "all_top_k": [],
            "categories_list": meta["categories"]
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    conditions = []
    params = []
    
    # Dynamic category filter
    if category and category.strip():
        conditions.append("category = ?")
        params.append(category.strip())
        
    # Dynamic price filter (exact match within budget + 5% buffer for edge cases)
    if max_price and max_price > 0:
        conditions.append("price_clean > 0 AND price_clean <= ?")
        params.append(max_price * 1.05)
        
    # Dynamic brand filter
    if brand and brand.strip():
        conditions.append("LOWER(brand) = LOWER(?)")
        params.append(brand.strip())
        
    sql = "SELECT * FROM all_products"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
        
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    
    status = "exact_match"
    sql_used = sql
    fallback_params = []
    
    # If exact budget filtering returned 0 results, execute budget fallback query to suggest nearest upgrades
    if not rows and max_price and max_price > 0:
        status = "budget_fallback"
        sql_fallback = "SELECT * FROM all_products WHERE price_clean > ?"
        fallback_params.append(max_price)
        if category and category.strip():
            sql_fallback += " AND category = ?"
            fallback_params.append(category.strip())
        if brand and brand.strip():
            sql_fallback += " AND LOWER(brand) = LOWER(?)"
            fallback_params.append(brand.strip())
        sql_fallback += " ORDER BY price_clean ASC LIMIT 10"
        cursor.execute(sql_fallback, fallback_params)
        rows = cursor.fetchall()
        sql_used = sql_fallback
        
        # If even with brand/category filter no fallback rows found, drop brand constraint
        if not rows and brand and category:
            sql_fallback2 = "SELECT * FROM all_products WHERE price_clean > ? AND category = ? ORDER BY price_clean ASC LIMIT 10"
            cursor.execute(sql_fallback2, [max_price, category.strip()])
            rows = cursor.fetchall()
            sql_used = sql_fallback2
            fallback_params = [max_price, category.strip()]

    conn.close()
    
    if not rows:
        status = "no_products_found"
        
    # Convert and score
    results = []
    for r in rows:
        prod = dict(r)
        prod["_score"] = score_product(prod, query, priority_features)
        # Normalize fields for UI transparency table
        prod["name"] = f"Model {prod.get('model_code') or prod.get('sku', 'N/A')} - {prod.get('brand', '')}" if prod.get('model_code') or prod.get('sku') else str(prod.get('key_specs_summary', 'Sản phẩm'))
        prod["price"] = prod.get("price_clean") or 0
        results.append(prod)
        
    # If exact match, sort by semantic score descending, then price ascending
    # If budget_fallback, sort primarily by price ascending so cheapest/closest options come first
    if status == "budget_fallback":
        results.sort(key=lambda x: (float(x.get("price_clean") or 999999999), -x["_score"]))
    else:
        results.sort(key=lambda x: (x["_score"], -float(x.get("price_clean") or 0)), reverse=True)
        
    top_items = results[:top_k]
    
    # Format sql query string for UI display
    display_sql = sql_used
    display_params = fallback_params if status == "budget_fallback" else params
    for p in display_params:
        if isinstance(p, str):
            display_sql = display_sql.replace("?", f"'{p}'", 1)
        else:
            display_sql = display_sql.replace("?", str(p), 1)
            
    return {
        "status": status,
        "sql_query": display_sql,
        "total_matches_found": len(results),
        "top_3_products": top_items[:3],
        "all_top_k": top_items
    }
