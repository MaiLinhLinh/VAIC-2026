from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source: str
    detail: str | None = None
    as_of: str | None = None


class SourcedValue(BaseModel):
    available: bool
    value: Any = None
    unit: str | None = None
    provenance: Provenance | None = None
    note: str | None = None

    @classmethod
    def of(cls, value, source, unit=None, detail=None, as_of=None) -> "SourcedValue":
        return cls(available=True, value=value, unit=unit,
                   provenance=Provenance(source=source, detail=detail, as_of=as_of))

    @classmethod
    def missing(cls, note: str = "chưa có dữ liệu") -> "SourcedValue":
        return cls(available=False, value=None, note=note)


class Product(BaseModel):
    category: str
    category_code: str
    model_code: str
    sku: str
    brand: str
    display_name: str
    price: SourcedValue
    original_price: SourcedValue
    sale_price: SourcedValue
    specs: dict[str, SourcedValue] = Field(default_factory=dict)
    spec_doc: str = ""
    promo_text: str | None = None
    productidweb: str | None = None
    rating: SourcedValue = Field(default_factory=SourcedValue.missing)
    quantity_sold: SourcedValue = Field(default_factory=SourcedValue.missing)
    warranty: SourcedValue = Field(default_factory=SourcedValue.missing)
    url: str | None = None
    image_url: str | None = None
    crawl_promotion: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    rating: SourcedValue = Field(default_factory=SourcedValue.missing)
    quantity_sold: SourcedValue = Field(default_factory=SourcedValue.missing)
    warranty: SourcedValue = Field(default_factory=SourcedValue.missing)
    crawl_promotion: str | None = None
    url: str | None = None
    image_url: str | None = None

    def number(self, field: str) -> float | None:
        sv = self.specs.get(field)
        if sv is None or not sv.available:
            return None
        return sv.value if isinstance(sv.value, (int, float)) else None


class NeedProfile(BaseModel):
    category: str | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    prefs: list[str] = Field(default_factory=list)
    demographics: dict[str, str] = Field(default_factory=dict)
    known: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    def merge(self, other: "NeedProfile") -> "NeedProfile":
        out = self.model_copy(deep=True)
        for f in ("category", "budget_min", "budget_max"):
            v = getattr(other, f)
            if v is not None:
                setattr(out, f, v)
        out.constraints = {**out.constraints, **other.constraints}
        out.demographics = {**out.demographics, **other.demographics}
        out.prefs = list(dict.fromkeys(out.prefs + other.prefs))
        out.assumptions = list(dict.fromkeys(out.assumptions + other.assumptions))
        out.known = list(dict.fromkeys(out.known + other.known))
        return out


class SlotQuestion(BaseModel):
    slot: str
    text: str
    importance: int


class ScoredProduct(BaseModel):
    product: Product
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    matched: list[str] = Field(default_factory=list)


class ExcludedGroup(BaseModel):
    label: str
    reason: str


class Recommendation(BaseModel):
    top3: list[ScoredProduct]
    excluded: ExcludedGroup | None = None
    assumptions: list[str] = Field(default_factory=list)


class FactLine(BaseModel):
    label: str
    value: str
    source: str


class ReviewItem(BaseModel):
    author: str | None = None
    rating: float | None = None
    content: str


class FactCard(BaseModel):
    title: str
    lines: list[FactLine] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    productidweb: str | None = None
    image_url: str | None = None
    product_link: str | None = None
    stock_status: str | None = None      # "Còn hàng" / "Hết hàng" (cào từ dienmayxanh.com)
    rating: float | None = None          # điểm đánh giá trung bình
    review_count: int | None = None      # số lượt đánh giá
    installment: str | None = None       # thông tin trả góp
    reviews: list[ReviewItem] = Field(default_factory=list)


class ComparisonCell(BaseModel):
    value: str                      # "12.400.000đ", "300 kWh/năm", hoặc "chưa có dữ liệu"
    available: bool = True
    is_best: bool = False           # ứng viên tốt nhất theo tiêu chí của hàng này
    status: str | None = None       # "good" / "warn" / "bad" — đèn tín hiệu cho hàng theo nhu cầu
    verdict: str | None = None      # nhãn ngắn: "Dư sức mua", "Vượt trội", ... (rút ra từ số liệu thật)
    detail: str | None = None       # câu giải thích, luôn dựng từ giá trị thật trong DB, không suy diễn


class ComparisonRow(BaseModel):
    label: str                      # "Giá", "Điện năng tiêu thụ", "Thương hiệu"
    unit: str | None = None
    source: str                     # "catalog" / "thông số nhà sản xuất"
    cells: list[ComparisonCell] = Field(default_factory=list)   # 1 ô / sản phẩm, cùng thứ tự với products
    better: str | None = None       # gợi ý đọc: "giá thấp hơn tốt hơn", ...
    is_need_row: bool = False       # true nếu hàng gắn với ngân sách/nhu cầu khách nêu (render kiểu đèn tín hiệu)


class ComparisonTable(BaseModel):
    products: list[str] = Field(default_factory=list)          # tên cột (display_name các ứng viên)
    rows: list[ComparisonRow] = Field(default_factory=list)
    tradeoff: list[str] | None = None   # 1 câu đánh đổi / sản phẩm, cùng thứ tự với products


class AdviceResult(BaseModel):
    message: str
    cards: list[FactCard] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    comparison: ComparisonTable | None = None
