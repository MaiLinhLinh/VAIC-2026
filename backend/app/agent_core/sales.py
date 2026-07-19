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

from typing import Optional

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
