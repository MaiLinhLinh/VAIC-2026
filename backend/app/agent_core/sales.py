from __future__ import annotations

"""Câu chốt bán hàng dùng chung: gỡ trước rào cản phổ biến nhất (phí giao hàng/lắp đặt)
rồi mời khách sang bước đặt hàng.

Số liệu lấy đúng từ app/data/policies/giao-hang-lap-dat.md mục "Phí giao hàng"
(cập nhật 01/06/2025) — chọn đúng nhánh lắp đặt/không lắp đặt theo nhóm hàng và mốc
giỏ hàng theo giá sản phẩm, KHÔNG suy diễn hộ nhóm hàng chưa có trong tài liệu.

Lưu ý quan trọng: mục "Phí giao hàng" chỉ nêu phí GIAO, không phải phí lắp đặt. Với
máy lạnh, vật tư lắp đặt (ống đồng, gas, công khoan…) tính RIÊNG theo khảo sát thực tế
(mục "Phí vật tư lắp đặt máy lạnh tham khảo") — TUYỆT ĐỐI không nói "lắp đặt miễn phí"
cho máy lạnh, chỉ nói phí giao được miễn/thu theo mốc giỏ hàng.
"""

from typing import Any, Dict, List, Optional

from app.nlu.preprocess import strip_accents

# Nhóm hàng lắp đặt / không lắp đặt đúng như liệt kê trong giao-hang-lap-dat.md.
_INSTALL_CATEGORIES = {"tu lanh", "may lanh", "may giat", "may nuoc nong"}
_NON_INSTALL_CATEGORIES = {
    "may tinh bang", "micro karaoke", "micro thu am dien thoai",
    "dong ho thong minh", "may tinh de ban", "man hinh may tinh", "may in",
}

# Ghi chú riêng cho nhóm có chi phí phát sinh tách biệt khỏi phí giao hàng.
_INSTALL_EXTRA_NOTE = {
    "may lanh": "Vật tư lắp đặt như ống đồng, gas, công khoan nếu phát sinh sẽ khảo sát và tính riêng.",
    "tu lanh": "Tủ side by side nếu nhà chật cần thuê cẩu thì khách chịu phí thuê cẩu.",
    "may giat": "Máy lồng ngang cỡ lớn nếu nhà chật cần thuê cẩu thì khách chịu phí thuê cẩu.",
}


def _flat(category: Optional[str]) -> str:
    return strip_accents((category or "").strip().lower())


def shipping_fee_line(category: Optional[str], price: float) -> Optional[tuple[str, bool]]:
    """Trả (câu phí GIAO HÀNG, is_install) đúng theo mốc giỏ hàng trong tài liệu. None nếu
    nhóm hàng không nằm trong danh sách đã xác nhận (tránh bịa phí cho nhóm chưa rõ)."""
    cat = _flat(category)
    price = price or 0.0
    if cat in _INSTALL_CATEGORIES:
        if price >= 5_000_000:
            return "phí giao hàng được miễn phí trong 10km đầu, từ km 11 tính thêm 5.000đ/km", True
        return "phí giao hàng 50.000đ cho 10km đầu, từ km 11 tính thêm 5.000đ/km", True
    if cat in _NON_INSTALL_CATEGORIES:
        if price >= 2_000_000:
            return "phí giao hàng tiêu chuẩn được miễn phí trong bán kính giao", False
        if price >= 500_000:
            return "phí giao hàng được miễn phí trong 10km đầu, từ km 11 tính thêm 5.000đ/km", False
        return "phí giao hàng 20.000đ cho 10km đầu, từ km 11 tính thêm 5.000đ/km", False
    return None


def closing_hook(category: Optional[str] = None, price: float = 0.0,
                 addr: str = "anh/chị", self_term: str = "em") -> str:
    hit = shipping_fee_line(category, price)
    if hit:
        fee, is_install = hit
        label = "giao hàng, lắp đặt" if is_install else "giao hàng"
        extra = _INSTALL_EXTRA_NOTE.get(_flat(category), "") if is_install else ""
        sentences = [f"Về {label}: máy này {fee} ạ."]
        if extra:
            sentences.append(extra)
        sentences.append(f"Phí thực tế còn tùy địa chỉ, hệ thống báo rõ khi {addr} đặt — "
                          f"{addr} muốn {self_term} hướng dẫn đặt hàng luôn không ạ?")
        return " ".join(sentences)
    return (f"Phí giao hàng/lắp đặt sẽ hiện rõ theo địa chỉ khi {addr} đặt hàng ạ — "
            f"{addr} muốn {self_term} hướng dẫn đặt hàng luôn không ạ?")


"""Bán chéo (cross-sell): khi khách vừa chốt/xem kỹ một máy, gợi ý thêm 1 sản phẩm THẬT
trong catalog thuộc ngành hàng bổ trợ hợp lý (VD máy giặt <-> máy sấy). Ánh xạ chỉ khai báo
cặp ngành hàng có ý nghĩa mua kèm thực tế trong 14 category của catalog — KHÔNG suy đoán
phụ kiện không kinh doanh (tai nghe, ốp lưng...). Sản phẩm gợi ý luôn lấy giá/tên thật từ DB."""

_CROSS_SELL_MAP: Dict[str, List[str]] = {
    "may giat": ["may say quan ao"],
    "may say quan ao": ["may giat"],
    "tu lanh": ["tu mat/tu dong"],
    "tu mat/tu dong": ["tu lanh"],
    "may tinh de ban": ["man hinh may tinh", "may in"],
    "man hinh may tinh": ["may tinh de ban"],
    "may in": ["may tinh de ban"],
    "micro karaoke": ["micro thu am"],
    "micro thu am": ["micro karaoke"],
}


def _match_catalog_category(name: str, categories: List[str]) -> Optional[str]:
    target = _flat(name)
    for c in categories:
        if _flat(c) == target:
            return c
    return None


def _row_sku(row: Dict[str, Any]) -> str:
    return str(row.get("model_code") or row.get("sku") or "")


def cross_sell_suggestion(category: Optional[str], price: float, db_path: Optional[str] = None,
                          exclude_sku: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Tìm 1 sản phẩm ngành hàng bổ trợ THẬT trong catalog, giá không vượt quá 1.5 lần giá
    máy đang mua (giữ combo hợp lý về ngân sách). None nếu không có ánh xạ hoặc catalog
    không có ứng viên phù hợp (không bịa gợi ý)."""
    candidates = _CROSS_SELL_MAP.get(_flat(category), [])
    if not candidates:
        return None
    from app.agent_core.retriever import get_catalog_metadata, search_products
    categories = get_catalog_metadata(db_path)["categories"]
    max_price = price * 1.5 if price else None
    for cand in candidates:
        real_cat = _match_catalog_category(cand, categories)
        if not real_cat:
            continue
        res = search_products(query="", category=real_cat, max_price=max_price,
                              top_k=5, db_path=db_path)
        rows = [r for r in (res.get("top_3_products") or res.get("all_top_k") or [])
                if _row_sku(r) != exclude_sku and float(r.get("price_clean") or 0) > 0]
        if rows:
            rows.sort(key=lambda r: float(r["price_clean"]))
            return rows[0]
    return None


def cross_sell_line(row: Dict[str, Any], addr: str = "anh/chị", self_term: str = "em") -> str:
    """Câu gợi mở mua kèm, luôn kèm TÊN NGÀNH HÀNG + tên + giá thật lấy từ catalog. Bắt buộc nêu
    ngành hàng vì product_display_name() chỉ trả hãng+mã (VD "Casper 179074"), không tự nói lên
    đây là loại máy gì -> thiếu ngữ cảnh khiến gợi ý trông như sản phẩm không liên quan."""
    from app.agent_core.presenters import product_display_name
    from app.advice.provenance import format_vnd
    name = product_display_name(row)
    category = (row.get("category") or "").strip()
    label = f"{category} {name}" if category and category.lower() not in name.lower() else name
    price = float(row.get("price_clean") or 0)
    price_txt = format_vnd(int(price)) if price > 0 else "chưa có dữ liệu giá"
    return (f"Nhân tiện, nhiều khách hay mua kèm {label} (giá {price_txt}, nguồn: catalog) để "
            f"dùng chung combo tiện hơn — {addr} có muốn {self_term} giới thiệu thêm không ạ?")


# Lưới từ khoá nhận diện khách CHỐT ĐƠN (xác nhận mua) — đường chính để ghi nhận lịch sử mua
# hàng trong phiên, phục vụ chăm sóc sau mua (bảo hành/ưu đãi tra theo đúng máy đã mua).
_ORDER_CONFIRM_KW = [
    "chot don", "chot may nay", "chot mua", "lay may nay", "mua may nay", "dat hang di",
    "dat hang giup", "xac nhan mua", "ok mua", "chot luon", "lay luon may nay",
    "mua luon may nay", "dong y mua", "dat may nay", "chot don hang", "minh lay may nay",
    "em lay may nay", "anh lay may nay", "chi lay may nay",
]

# Lưới nhận diện câu hỏi CHĂM SÓC SAU MUA: khách nhắc tới máy/đơn ĐÃ MUA trước đó (khác với
# hỏi bảo hành chung chung của một máy đang xem lần đầu).
_AFTERSALES_KW = [
    "may da mua", "don da mua", "da mua truoc do", "lan truoc mua", "may minh da mua",
    "may em da mua", "may toi da mua", "may đã mua", "don hang truoc", "may minh mua hom truoc",
    "may em mua hom truoc", "khach hang cu", "khach cu", "uu dai khach cu", "uu dai cho khach cu",
    "bao hanh may da mua", "bao hanh don da mua", "may da dat mua",
]


def is_order_confirmation(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(k in flat for k in _ORDER_CONFIRM_KW)


def is_aftersales_question(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(k in flat for k in _AFTERSALES_KW)
