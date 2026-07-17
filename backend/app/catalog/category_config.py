from __future__ import annotations
from typing import Literal
from dataclasses import dataclass, field

SpecKind = Literal["number", "range", "bool", "people", "text", "multi"]


@dataclass(frozen=True)
class SpecDef:
    field: str
    kind: SpecKind
    unit: str | None = None


@dataclass(frozen=True)
class SlotSpec:
    slot: str
    question: str
    importance: int          # 3 = critical, 2 = nên hỏi, 1 = tùy chọn
    maps_to: str
    kind: SpecKind


@dataclass(frozen=True)
class PrefSignal:
    field: str
    direction: Literal["min", "max"]
    weight: float = 1.0


@dataclass(frozen=True)
class ExclusionRule:
    when_pref: str
    label: str
    field: str
    empty_means_bad: bool = True


@dataclass(frozen=True)
class CategoryConfig:
    code: str
    sheet_name: str
    display: str
    name_template: str
    specs: list[SpecDef]
    spec_doc_fields: list[str]
    ask_slots: list[SlotSpec]
    pref_lexicon: dict[str, list[PrefSignal]]
    exclusion_rules: list[ExclusionRule] = field(default_factory=list)


CATEGORY_CONFIGS: dict[str, CategoryConfig] = {
    "tu_lanh": CategoryConfig(
        code="tu_lanh", sheet_name="Tủ Lạnh", display="Tủ lạnh",
        name_template="Tủ lạnh {brand} {Công nghệ tiết kiệm điện} {Dung tích tổng}",
        specs=[
            SpecDef("Dung tích tổng", "number", "lít"),
            SpecDef("Điện năng tiêu thụ", "number", "kWh/năm"),
            SpecDef("Số người sử dụng", "people", "người"),
            SpecDef("Kiểu dáng", "text"),
            SpecDef("Công nghệ tiết kiệm điện", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Kiểu dáng", "Công nghệ làm lạnh", "Công nghệ tiết kiệm điện",
                         "Công nghệ bảo quản thực phẩm", "Tiện ích"],
        ask_slots=[
            SlotSpec("số người", "Nhà mình khoảng mấy người dùng tủ lạnh này ạ?", 3, "số người", "people"),
            SlotSpec("kiểu dáng", "Anh/chị thích kiểu ngăn đá trên hay ngăn đá dưới ạ?", 1, "kiểu dáng", "text"),
        ],
        pref_lexicon={
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
            "dung tích lớn": [PrefSignal("Dung tích tổng", "max", 1.0)],
            "gia đình đông": [PrefSignal("Dung tích tổng", "max", 1.0)],
        },
        exclusion_rules=[
            ExclusionRule("tiết kiệm điện", "tủ lạnh không inverter", "Công nghệ tiết kiệm điện", True),
        ],
    ),
    "may_say": CategoryConfig(
        code="may_say", sheet_name="Máy sấy quần áo", display="Máy sấy quần áo",
        name_template="Máy sấy {brand} {Khối lượng tải chính}",
        specs=[
            SpecDef("Khối lượng tải chính", "number", "kg"),
            SpecDef("Điện năng tiêu thụ", "number"),
            SpecDef("Số người sử dụng", "people", "người"),
            SpecDef("Công nghệ", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Loại sản phẩm", "Công nghệ", "Tiện ích", "Cảm biến"],
        ask_slots=[
            SlotSpec("khối lượng", "Nhà mình cần sấy khoảng mấy kg mỗi lần ạ (nhà mấy người)?", 3, "khối lượng", "number"),
        ],
        pref_lexicon={
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
            "tải lớn": [PrefSignal("Khối lượng tải chính", "max", 1.0)],
        },
        exclusion_rules=[],
    ),
    "may_rua_chen": CategoryConfig(
        code="may_rua_chen", sheet_name="Máy rửa chén", display="Máy rửa chén",
        name_template="Máy rửa chén {brand} {Loại sản phẩm}",
        specs=[
            SpecDef("Độ ồn", "number", "dB"),
            SpecDef("Tiêu thụ nước", "range", "lít/lần"),
            SpecDef("Công suất đầu ra", "range", "W"),
            SpecDef("Loại sản phẩm", "text"),
            SpecDef("Công nghệ", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Loại sản phẩm", "Công nghệ", "Công nghệ sấy", "Chương trình", "Tiện ích"],
        ask_slots=[
            SlotSpec("số bữa", "Nhà mình khoảng mấy người ăn để em tính số bộ chén phù hợp ạ?", 3, "số người", "people"),
        ],
        pref_lexicon={
            "ít ồn": [PrefSignal("Độ ồn", "min", 1.0)],
            "tiết kiệm nước": [PrefSignal("Tiêu thụ nước", "min", 1.0)],
        },
        exclusion_rules=[],
    ),
    "tu_mat": CategoryConfig(
        code="tu_mat", sheet_name="Tủ mát, tủ đông", display="Tủ mát / tủ đông",
        name_template="{Loại sản phẩm} {brand} {Dung tích tổng}",
        specs=[
            SpecDef("Dung tích tổng", "number", "lít"),
            SpecDef("Điện năng tiêu thụ", "number"),
            SpecDef("Độ ồn", "number", "dB"),
            SpecDef("Loại sản phẩm", "text"),
            SpecDef("Số cửa", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Loại sản phẩm", "Công nghệ", "Tiện ích", "Số cửa"],
        ask_slots=[
            SlotSpec("dung tích", "Anh/chị cần dung tích khoảng bao nhiêu lít, hay để em gợi ý theo nhu cầu ạ?", 2, "dung tích", "number"),
        ],
        pref_lexicon={
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
            "dung tích lớn": [PrefSignal("Dung tích tổng", "max", 1.0)],
            "ít ồn": [PrefSignal("Độ ồn", "min", 1.0)],
        },
        exclusion_rules=[],
    ),
    "dong_ho": CategoryConfig(
        code="dong_ho", sheet_name="Đồng hồ thông minh", display="Đồng hồ thông minh",
        name_template="Đồng hồ {brand} {Kích thước mặt}",
        specs=[
            SpecDef("Dung lượng pin", "number", "mAh"),
            SpecDef("Kích thước màn hình", "number", "inch"),
            SpecDef("SIM", "text"),
            SpecDef("Thực hiện cuộc gọi", "text"),
            SpecDef("Theo dõi sức khoẻ", "multi"),
            SpecDef("Môn thể thao", "multi"),
        ],
        spec_doc_fields=["Theo dõi sức khoẻ", "Môn thể thao", "Tiện ích khác",
                         "Thực hiện cuộc gọi", "Chuẩn chống nước, bụi"],
        ask_slots=[
            SlotSpec("người dùng", "Đồng hồ này dùng cho ai ạ (trẻ em, người lớn, người tập thể thao)?", 3, "người dùng", "text"),
        ],
        pref_lexicon={
            "pin lâu": [PrefSignal("Dung lượng pin", "max", 1.0)],
            "màn hình lớn": [PrefSignal("Kích thước màn hình", "max", 1.0)],
        },
        exclusion_rules=[],
    ),
    "man_hinh": CategoryConfig(
        code="man_hinh", sheet_name="Màn hình máy tính", display="Màn hình máy tính",
        name_template="Màn hình {brand} {Kích thước màn hình} {Tấm nền}",
        specs=[
            SpecDef("Kích thước màn hình", "number", "inch"),
            SpecDef("Thời gian đáp ứng", "number", "ms"),
            SpecDef("Điện năng tiêu thụ", "number", "W"),
            SpecDef("Tấm nền", "text"),
            SpecDef("Độ phân giải", "text"),
            SpecDef("Tiện ích", "multi"),
        ],
        spec_doc_fields=["Tấm nền", "Độ phân giải", "Màn hình hiển thị", "Tiện ích", "Loại màn hình"],
        ask_slots=[
            SlotSpec("mục đích", "Anh/chị dùng màn hình chủ yếu để làm gì ạ (văn phòng, chơi game, đồ họa)?", 3, "mục đích", "text"),
            SlotSpec("kích thước", "Anh/chị muốn màn khoảng bao nhiêu inch ạ?", 2, "kích thước", "number"),
        ],
        pref_lexicon={
            "màn hình lớn": [PrefSignal("Kích thước màn hình", "max", 1.0)],
            "chơi game": [PrefSignal("Thời gian đáp ứng", "min", 1.0)],
            "phản hồi nhanh": [PrefSignal("Thời gian đáp ứng", "min", 1.0)],
            "tiết kiệm điện": [PrefSignal("Điện năng tiêu thụ", "min", 1.0)],
        },
        exclusion_rules=[],
    ),
}

SHEET_TO_CODE: dict[str, str] = {c.sheet_name: c.code for c in CATEGORY_CONFIGS.values()}


def config_for(code: str) -> CategoryConfig:
    return CATEGORY_CONFIGS[code]
