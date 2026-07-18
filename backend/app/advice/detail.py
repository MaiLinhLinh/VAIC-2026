from __future__ import annotations
from app.schemas import Product, FactCard, FactLine, AdviceResult
from app.llm.client import LLMClient
from app.advice.provenance import format_vnd, facts_for_llm, _ALWAYS_MISSING
from app.advice.verify import verify_advice, is_grounded
from app.nlu.preprocess import strip_accents
from app.nlu.preprocess import parse_budget_vnd, wants_call
from app.catalog.capabilities import call_status, product_supports_call

# Cột raw KHÔNG phải "thông số" để hiển thị (id, giá & quà đã tách riêng)
_SKIP_RAW = {"model_code", "sku", "productidweb", "category_code", "brand_id", "brand",
             "giá gốc", "giá khuyến mãi", "khuyến mãi quà"}

# Tham chiếu theo vị trí (đã bỏ dấu). Cụm dài đặt trước để ưu tiên.
_POSITION: dict[str, int] = {
    "dau tien": 0, "thu nhat": 0, "may 1": 0, "cai 1": 0, "so 1": 0, "thu 1": 0, "mau 1": 0,
    "thu hai": 1, "may 2": 1, "cai 2": 1, "so 2": 1, "thu 2": 1, "mau 2": 1, "o giua": 1,
    "cuoi cung": 2, "thu ba": 2, "may 3": 2, "cai 3": 2, "so 3": 2, "thu 3": 2, "mau 3": 2, "cuoi": 2,
}

_DETAIL_KW = ["chi tiet", "ky hon", "ky ve", "cu the", "thong so", "bao nhieu", "the nao",
              "co gi", "noi them", "noi ro", "bao hanh", "kich thuoc", "can nang", "khoi luong",
              "mau sac", "cong nghe", "tinh nang", "dung tich", "pin", "man hinh", "chi so",
              "co tot khong", "danh gia", "tim hieu", "xem them", "ra sao", "nhu the nao",
              "kieu dang", "xuat xu", "san xuat", "cong suat", "trong luong", "mau gi",
              "thi sao", "the con", "con nao", "diem manh", "uu diem", "nhuoc diem"]


def is_detail_question(message: str) -> bool:
    flat = strip_accents(message.lower())
    return any(kw in flat for kw in _DETAIL_KW)


def _fmt(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def resolve_product(message: str, products: list[Product]) -> Product | None:
    """Xác định khách đang nhắc tới sản phẩm nào trong danh sách đề xuất (vị trí / hãng / rẻ-đắt nhất)."""
    if not products:
        return None
    flat = strip_accents(message.lower())

    # 1) theo vị trí
    for key, idx in _POSITION.items():
        if key in flat and idx < len(products):
            return products[idx]

    # 2) theo brand (tên hãng xuất hiện trong câu; cần >=2 ký tự để tránh khớp nhầm)
    for p in products:
        b = strip_accents(p.brand.lower()).strip()
        if len(b) >= 2 and b in flat:
            return p

    # 3) theo mức giá được nhắc rõ (ví dụ "cái 150k có nghe gọi không")
    low, high = parse_budget_vnd(message)
    mentioned_price = high if high is not None else low
    if mentioned_price is not None:
        matches = [p for p in products if p.price.available and p.price.value == mentioned_price]
        if len(matches) == 1:
            return matches[0]

    # 4) theo superlative giá
    priced = [p for p in products if p.price.available]
    if priced and ("re nhat" in flat or "gia thap nhat" in flat or "gia tot nhat" in flat):
        return min(priced, key=lambda p: p.price.value)
    if priced and ("dat nhat" in flat or "cao cap nhat" in flat or "xin nhat" in flat):
        return max(priced, key=lambda p: p.price.value)

    return None


def build_full_fact_card(product: Product) -> FactCard:
    """Fact-sheet đầy đủ của MỘT sản phẩm: giá + toàn bộ cột thông số + quà kèm, mọi ô gắn nguồn."""
    lines: list[FactLine] = []
    missing: list[str] = []

    if product.price.available:
        detail = product.price.provenance.detail if product.price.provenance else None
        lines.append(FactLine(label="Giá", value=format_vnd(int(product.price.value)),
                              source="catalog" + (f" ({detail})" if detail else "")))
    else:
        missing.append("giá")
    lines.append(FactLine(label="Thương hiệu", value=product.brand, source="catalog"))

    for k, v in product.raw.items():
        if k in _SKIP_RAW or v is None:
            continue
        s = _fmt(v).strip()
        if not s:
            continue
        lines.append(FactLine(label=k, value=s, source="thông số nhà sản xuất"))

    if product.promo_text:
        lines.append(FactLine(label="Khuyến mãi/quà kèm", value=product.promo_text,
                              source="khuyến mãi (catalog)"))

    missing.extend(_ALWAYS_MISSING)
    return FactCard(title=f"Thông tin chi tiết: {product.display_name}", lines=lines, missing=missing)


_DETAIL_SYSTEM = (
    "Bạn là nhân viên tư vấn điện máy thân thiện, nói tiếng Việt bình dân. Khách đang hỏi kỹ về MỘT "
    "sản phẩm cụ thể. Bạn CHỈ được dùng dữ kiện trong phần FACTS; TUYỆT ĐỐI không bịa thông số, giá, "
    "khuyến mãi, tồn kho, đánh giá. Nếu thông tin khách hỏi không có trong FACTS, hãy nói thẳng "
    "'dạ em chưa có dữ liệu về ... ạ'. Trả lời thẳng vào câu hỏi của khách, ngắn gọn, thân thiện."
)


def _safe_summary(product: Product, card: FactCard) -> str:
    keep = [l for l in card.lines if l.label in ("Giá", "Thương hiệu")]
    head = "; ".join(f"{l.label} {l.value}" for l in keep) if keep else "thông tin cơ bản"
    return (f"Dạ về {product.display_name}: {head}. "
            "Anh/chị muốn biết thêm thông số cụ thể nào ạ?")


def answer_about_product(product: Product, question: str, llm: LLMClient) -> AdviceResult:
    """Trả lời sâu về 1 sản phẩm, grounded trong fact-sheet của nó; fail-closed nếu LLM bịa số."""
    card = build_full_fact_card(product)
    if wants_call(question):
        status = call_status(product)
        if status is None or strip_accents(status.lower()) == "dang cap nhat":
            message = (f"Dạ, catalog chưa có dữ liệu xác nhận {product.display_name} "
                       "có nghe gọi được hay không ạ.")
        elif product_supports_call(product):
            message = (f"Dạ có ạ. Catalog ghi khả năng gọi của {product.display_name}: "
                       f"{status}.")
        else:
            message = (f"Dạ không ạ. Catalog ghi khả năng gọi của {product.display_name}: "
                       f"{status}. Em không xem mẫu này là đáp ứng nhu cầu nghe gọi.")
        return AdviceResult(message=message, cards=[card], assumptions=[], warnings=[])
    facts = facts_for_llm([card])
    user = (f"Khách hỏi về \"{product.display_name}\": \"{question}\"\n\n"
            f"FACTS:\n{facts}\n\nTrả lời khách theo đúng quy tắc.")
    message = llm.complete_text(_DETAIL_SYSTEM, user)
    result = verify_advice(AdviceResult(message=message, cards=[card], assumptions=[], warnings=[]))
    if not is_grounded(result):
        result.message = _safe_summary(product, card)  # fail-closed
    return result
