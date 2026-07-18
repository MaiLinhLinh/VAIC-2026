"""Làm sạch spec sheet (Spec_cate_gia.xlsx) trước khi chuẩn hoá thành Product.

Quy tắc chung (mọi sheet):
- Placeholder ("Đang cập nhật", "Hãng không công bố", "null"...) -> chưa có dữ liệu.
- Strip ký tự Unicode vô hình (LRM, ZWSP, NBSP...).
- "Không/Không có" ở cột bắt-buộc-phải-có -> chưa có dữ liệu; ở cột tuỳ chọn giữ
  nguyên (vắng mặt thật).
- Cột nhị phân quy về "Có"/"Không có" (+ chi tiết nếu câu gốc chứa thêm thông tin).
- Cửa sổ hợp lý: giá trị số ngoài khoảng vật-lý-khả-dĩ -> chưa có dữ liệu + báo cáo.
- Đơn vị chuẩn của cột: quy đổi chỉ khi cùng đại lượng với hệ số xác định.
- Mọi bất thường được đếm vào báo cáo, không sửa ngầm.

Quyết định thiết kế được ghi tại CONTEXT.md (glossary) — cấu hình từng sheet ở
SHEET_RULES cuối file. Khi derive ghi đè một ô, nguyên văn gốc được giữ ở cột
"<tên cột> (nguyên văn)".
"""
from __future__ import annotations
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

_INVISIBLE = re.compile("[​‌‍‎‏﻿]")
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
# "32-42 dB" là khoảng 32..42, không phải 32 và -42: dash giữa hai số là khoảng
_POS_NUM = re.compile(r"\d+(?:[.,]\d+)?")

PLACEHOLDERS = {"đang cập nhật", "hãng không công bố", "null", "none", "nan"}
_NEG = {"không", "không có"}


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def clean_cell(v):
    """Chuẩn hoá một ô: strip ký tự vô hình, gom khoảng trắng, placeholder -> None."""
    if v is None or _is_nan(v):
        return None
    if not isinstance(v, str):
        return v
    t = _INVISIBLE.sub("", v).replace(" ", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if t.lower() in PLACEHOLDERS or not t:
        return None
    return t


def _num(tok: str) -> float:
    return float(tok.replace(",", "."))


def _numbers(v, positive: bool = False) -> list[float]:
    if v is None:
        return []
    if isinstance(v, (int, float)) and not _is_nan(v):
        return [float(v)]
    rx = _POS_NUM if positive else _NUM
    return [_num(m) for m in rx.findall(str(v))]


def _is_neg(v) -> bool:
    return isinstance(v, str) and v.strip().lower() in _NEG


# ---------------------------------------------------------------- helpers

def _apply_window(row: dict, col: str, lo: float, hi: float,
                  report: Counter, tag: str) -> None:
    v = row.get(col)
    if v is None or _is_neg(v):
        return
    nums = _numbers(v, positive=True)
    if not nums:
        return
    if any(n < lo or n > hi for n in nums):
        row[f"{col} (nguyên văn)"] = v
        row[col] = None
        report[tag] += 1


def _binary(row: dict, col: str) -> None:
    v = row.get(col)
    if not isinstance(v, str):
        return
    t = v.strip()
    low = t.lower()
    if low.startswith("không"):
        row[col] = "Không có"
    elif low.startswith("có") or low == "co":
        detail = t[2:].strip(" -–,:")
        row[col] = f"Có ({detail})" if detail else "Có"
    # giá trị khác ("FaceTime"...) hiểu là Có + chi tiết
    else:
        row[col] = f"Có ({t})"


def _canonical_timebase(rows: list[dict], col: str, factors: dict[str, float],
                        report: Counter, tag: str) -> None:
    """Quy đổi đơn-vị-có-mốc-thời-gian về đơn vị phổ biến nhất trong cột.

    factors: đơn vị -> hệ số quy về đơn vị gốc chung (vd {"kwh/năm": 1,
    "kwh/ngày": 365} nghĩa là 1 kWh/ngày = 365 kWh/năm-gốc).
    """
    def unit_of(text: str) -> str | None:
        low = text.lower()
        for u in sorted(factors, key=len, reverse=True):
            if u in low:
                return u
        return None

    counts: Counter = Counter()
    for r in rows:
        v = r.get(col)
        if isinstance(v, str):
            u = unit_of(v)
            if u:
                counts[u] += 1
    if len(counts) < 2:
        return
    dominant = counts.most_common(1)[0][0]
    for r in rows:
        v = r.get(col)
        if not isinstance(v, str):
            continue
        u = unit_of(v)
        if u is None or u == dominant:
            continue
        nums = _numbers(v)
        if not nums:
            continue
        converted = nums[0] * factors[u] / factors[dominant]
        r[f"{col} (nguyên văn)"] = v
        r[col] = f"{converted:g} {dominant}"
        report[tag] += 1


def _relabel(row: dict, src: str, dst: str) -> None:
    """Gắn lại nhãn ngữ nghĩa: chép giá trị sang tên đúng nghĩa, giữ cột gốc."""
    v = row.get(src)
    if v is not None and not _is_neg(v):
        row[dst] = v


# ------------------------------------------------- transforms theo sheet

def _t_tu_lanh(rows: list[dict], report: Counter) -> None:
    for r in rows:
        # "128 - 152 Wh": Wh không rõ mốc thời gian, không quy đổi được sang kWh/năm
        v = r.get("Điện năng tiêu thụ")
        if isinstance(v, str) and "wh" in v.lower() and "kwh" not in v.lower():
            r["Điện năng tiêu thụ (nguyên văn)"] = v
            r["Điện năng tiêu thụ"] = None
            report["tu_lanh.dien_nang_wh_khong_ro_moc"] += 1


def _noise_min(rows: list[dict], report: Counter, tag: str,
               lo: float = 15, hi: float = 70) -> None:
    """Độ ồn = một giá trị duy nhất: số dB nhỏ nhất trong chuỗi."""
    for r in rows:
        v = r.get("Độ ồn")
        if not isinstance(v, str):
            continue
        nums = _numbers(v, positive=True)
        if not nums:
            continue
        best = min(nums)
        r["Độ ồn (nguyên văn)"] = v
        if lo <= best <= hi:
            r["Độ ồn"] = f"{best:g} dB"
        else:
            r["Độ ồn"] = None
            report[tag] += 1


_SAO = re.compile(r"(\d)\s*sao", re.I)
_HIEU_SUAT = re.compile(r"hiệu suất năng lượng\s*([\d.,]+)", re.I)


def _t_may_lanh(rows: list[dict], report: Counter) -> None:
    _noise_min(rows, report, "may_lanh.do_on_ngoai_cua_so")
    for r in rows:
        v = r.get("Nhãn năng lượng")
        if isinstance(v, str):
            m = _SAO.search(v)
            if m:
                r["Nhãn năng lượng (sao)"] = m.group(1)
            m = _HIEU_SUAT.search(v)
            if m:
                eff = _num(m.group(1))
                if 2 <= eff <= 8:
                    r["Hiệu suất năng lượng"] = f"{eff:g}"
                else:
                    report["may_lanh.hieu_suat_ngoai_cua_so"] += 1


_LONG_CUA = {"lồng đứng": "cửa trên", "lồng ngang": "cửa trước"}


def _t_may_giat(rows: list[dict], report: Counter) -> None:
    for r in rows:
        long_giat = r.get("Lồng giặt")
        loai = r.get("Loại sản phẩm")
        if isinstance(long_giat, str) and isinstance(loai, str):
            expect = _LONG_CUA.get(long_giat.strip().lower())
            if expect and loai.strip().lower() in _LONG_CUA.values() \
                    and loai.strip().lower() != expect:
                report["may_giat.long_giat_mau_thuan_loai_sp"] += 1


def _t_may_say(rows: list[dict], report: Counter) -> None:
    # "Điện năng tiêu thụ" của máy sấy thực chất là công suất (W)
    for r in rows:
        _relabel(r, "Điện năng tiêu thụ", "Công suất")
    for r in rows:
        _apply_window(r, "Công suất", 300, 4000, report, "may_say.cong_suat_ngoai_cua_so")


_BO_CHEN = re.compile(r"([\d.,]+)\s*bộ", re.I)
_BUA_AN = re.compile(r"(\d+(?:\s*-\s*\d+)?)\s*bữa", re.I)


def _t_may_rua_chen(rows: list[dict], report: Counter) -> None:
    for r in rows:
        v = r.get("Số lượng")
        if not isinstance(v, str):
            continue
        m = _BO_CHEN.search(v)
        if m:
            r["Số bộ chén"] = f"{_num(m.group(1)):g} bộ"
        m = _BUA_AN.search(v)
        if m:
            r["Số bữa ăn Việt"] = f"{m.group(1)} bữa"


def _t_tu_mat(rows: list[dict], report: Counter) -> None:
    _canonical_timebase(rows, "Điện năng tiêu thụ",
                        {"kwh/năm": 1, "kwh/ngày": 365},
                        report, "tu_mat.dien_nang_quy_doi_timebase")
    # cửa sổ hợp lý theo đơn vị thực tế của ô (đơn vị trội của cột là kWh/ngày)
    for r in rows:
        v = r.get("Điện năng tiêu thụ")
        if isinstance(v, str):
            low = v.lower()
            lo, hi = (0.1, 15) if "kwh/ngày" in low else (50, 1500)
            _apply_window(r, "Điện năng tiêu thụ", lo, hi,
                          report, "tu_mat.dien_nang_ngoai_cua_so")
        v = r.get("Nhiệt độ ngăn đông (độ C)")
        if isinstance(v, str):
            t = v.replace("℃", "°C")
            t = re.sub(r"^\s*dưới\s+", "≤ ", t, flags=re.I)
            r["Nhiệt độ ngăn đông (độ C)"] = t
    _noise_min(rows, report, "tu_mat.do_on_ngoai_cua_so")


_NGUOI_BINH = re.compile(r"khoảng\s*(\d+(?:\s*-\s*\d+)?)\s*người", re.I)


def _t_may_nuoc_nong(rows: list[dict], report: Counter) -> None:
    for r in rows:
        _binary(r, "Bơm trợ lực")
        v = r.get("Dung lượng dung tích")
        if isinstance(v, str):
            m = _NGUOI_BINH.search(v)
            if m:
                # sheet không có cột "Số người sử dụng" -> derive để lọc theo số người
                r["Số người sử dụng"] = f"{m.group(1)} người"


def _t_micro_karaoke(rows: list[dict], report: Counter) -> None:
    for r in rows:
        v = r.get("Tần số hoạt động")
        if not isinstance(v, str):
            continue
        low = v.lower()
        if "mhz" in low:
            r["Băng tần RF"] = v
        elif "hz" in low:  # kHz / Hz: dải đáp tuyến âm thanh
            nums = _numbers(v)
            # "70 - 15 kHz" giảm dần là vô lý -> số đầu là Hz bị nguồn cắt đơn vị
            if len(nums) == 2 and nums[0] > nums[1] and "khz" in low:
                r["Dải tần âm thanh"] = f"{nums[0]:g} Hz - {nums[1]:g} kHz"
                report["micro_karaoke.tan_so_suy_don_vi_hz"] += 1
            else:
                r["Dải tần âm thanh"] = v


_YEAR = re.compile(r"^(19|20)\d{2}$")


def _t_micro_thu_am(rows: list[dict], report: Counter) -> None:
    # Giá trị lệch cột theo từng dòng: GIỮ NGUYÊN (quyết định Q14.3), chỉ đếm.
    for r in rows:
        for col in ("Nhiệt độ hoạt động bộ phát", "Nhiệt độ hoạt động bộ thu"):
            v = r.get(col)
            if isinstance(v, str) and _YEAR.match(v.strip()):
                report["micro_thu_am.gia_tri_lech_cot"] += 1
        for col in ("Loại pin bộ phát", "Loại pin hộp sạc", "Loại pin bộ thu"):
            v = r.get(col)
            if isinstance(v, str) and v.strip().isdigit():
                report["micro_thu_am.gia_tri_lech_cot"] += 1


_ATM = re.compile(r"([\d.,]+)\s*atm", re.I)


def _t_dong_ho(rows: list[dict], report: Counter) -> None:
    _canonical_timebase(rows, "Thời gian sử dụng",
                        {"giờ": 1, "tiếng": 1, "ngày": 24},
                        report, "dong_ho.thoi_gian_quy_doi_timebase")
    for r in rows:
        _apply_window(r, "Chu vi cổ tay", 10, 30, report,
                      "dong_ho.chu_vi_nhiem_model_code")
        v = r.get("Chuẩn chống nước, bụi")
        if isinstance(v, str):
            m = _ATM.search(v)
            if m:
                r["Chống nước (ATM)"] = f"{_num(m.group(1)):g} ATM"


def _has(r: dict, col: str) -> bool:
    v = r.get(col)
    return v is not None and not _is_neg(v)


def _t_may_tinh_ban(rows: list[dict], report: Counter) -> None:
    for r in rows:
        # Máy bộ lắp sẵn (Singpc, Rosa...) cũng ghi thông số case/mainboard kèm theo,
        # nên chỉ xét dấu hiệu linh kiện khi dòng THIẾU toàn bộ cấu phần máy hoàn chỉnh
        complete = (_has(r, "Công nghệ CPU") or _has(r, "RAM")
                    or _has(r, "Ổ cứng") or _has(r, "Hệ điều hành"))
        if complete:
            kind = "máy hoàn chỉnh"
        elif _has(r, "Chip đồ họa (GPU)") or _has(r, "Bộ nguồn đề xuất"):
            kind = "linh kiện (card đồ hoạ)"
        elif _has(r, "Model Mainboard") or _has(r, "Socket (mainboard)"):
            kind = "linh kiện (mainboard)"
        elif _has(r, "Hỗ trợ mainboard") or _has(r, "Loại Case"):
            kind = "linh kiện (case)"
        elif _has(r, "Tốc độ (RPM)"):
            kind = "linh kiện (tản nhiệt/quạt)"
        else:
            kind = "máy hoàn chỉnh"
        r["Loại hàng"] = kind
        if kind != "máy hoàn chỉnh":
            report["may_tinh_ban.linh_kien"] += 1
        # "120 GB/s memory bandwidth" là băng thông bộ nhớ, không phải tốc độ CPU
        v = r.get("Tốc độ CPU")
        if isinstance(v, str) and _numbers(v) and "ghz" not in v.lower():
            r["Tốc độ CPU (nguyên văn)"] = v
            r["Tốc độ CPU"] = None
            report["may_tinh_ban.toc_do_cpu_khong_phai_ghz"] += 1


_CONTRAST = re.compile(r"^[\d.,]+\s*:\s*1$")


def _t_man_hinh(rows: list[dict], report: Counter) -> None:
    for r in rows:
        _relabel(r, "Số lượng", "Số màu hiển thị")
        v = r.get("Độ tương phản tĩnh")
        if isinstance(v, str) and not _CONTRAST.match(v.strip()):
            r["Độ tương phản tĩnh (nguyên văn)"] = v
            r["Độ tương phản tĩnh"] = None
            report["man_hinh.tuong_phan_sai_khuon"] += 1


_TOC_DO_BW = re.compile(r"([\d.,]+)\s*trang/phút\s*\(đen trắng\)", re.I)
_TOC_DO_MAU = re.compile(r"([\d.,]+)\s*trang/phút\s*\(màu\)", re.I)


def _t_may_in(rows: list[dict], report: Counter) -> None:
    for r in rows:
        _relabel(r, "Phụ kiện đi kèm", "Hiệu suất mực đi kèm")
        _relabel(r, "Kích thước phụ kiện", "Khổ giấy hỗ trợ")
        v = r.get("Tốc độ in")
        if isinstance(v, str):
            m = _TOC_DO_BW.search(v)
            if m:
                r["Tốc độ in đen trắng"] = f"{_num(m.group(1)):g} trang/phút"
            m = _TOC_DO_MAU.search(v)
            if m:
                r["Tốc độ in màu"] = f"{_num(m.group(1)):g} trang/phút"


_SAC_W = re.compile(r"^[\d.,]+\s*w$", re.I)


def _t_may_tinh_bang(rows: list[dict], report: Counter) -> None:
    for r in rows:
        if isinstance(r.get("brand"), str) and "apple" in r["brand"].lower():
            r["brand"] = "Apple"
        v = r.get("Phụ kiện đi kèm")
        if isinstance(v, str) and _SAC_W.match(v.strip()):
            r["Sạc kèm theo"] = v
        # Pin: Wh và mAh không quy đổi được (thiếu điện áp) -> hai thông số song song
        v = r.get("Dung lượng pin")
        if isinstance(v, str):
            low = v.lower()
            if "wh" in low and "mah" not in low:
                r["Pin (Wh)"] = v
            elif "mah" in low:
                r["Pin (mAh)"] = v
        # Dung lượng khả dụng: số trần, ngữ cảnh cột cho phép gán GB
        v = r.get("Dung lượng khả dụng")
        nums = _numbers(v)
        if v is not None and nums and not (isinstance(v, str) and "gb" in v.lower()):
            if 1 <= nums[0] <= 2000:
                r["Dung lượng khả dụng"] = f"{nums[0]:g} GB"
            else:
                r["Dung lượng khả dụng (nguyên văn)"] = v
                r["Dung lượng khả dụng"] = None
                report["may_tinh_bang.kha_dung_ngoai_cua_so"] += 1


# ---------------------------------------------------------------- cấu hình

@dataclass(frozen=True)
class SheetRules:
    drop: frozenset[str] = frozenset()
    # cột bắt-buộc-phải-có: "Không/Không có" nghĩa là thiếu dữ liệu
    mandatory_khong: frozenset[str] = frozenset()
    binary: frozenset[str] = frozenset()
    windows: dict[str, tuple[float, float]] = field(default_factory=dict)
    transform: Callable[[list[dict], Counter], None] | None = None


SHEET_RULES: dict[str, SheetRules] = {
    "Tủ Lạnh": SheetRules(
        mandatory_khong=frozenset({"Dung tích tổng", "Dung tích ngăn lạnh",
                                   "Dung tích sử dụng", "Sản xuất tại"}),
        windows={"Điện năng tiêu thụ": (100, 1000)},
        transform=_t_tu_lanh,
    ),
    "Máy lạnh": SheetRules(
        drop=frozenset({"Số lượng", "Dòng điện vào", "Điện năng tiêu thụ",
                        "Khối lượng máy", "Cao phụ kiện chính 2",
                        "Dài phụ kiện chính 2", "Độ dày phụ kiện chính 2"}),
        transform=_t_may_lanh,
    ),
    "Máy giặt": SheetRules(
        drop=frozenset({"Số lượng", "Cao"}),
        mandatory_khong=frozenset({"Lồng giặt"}),
        windows={"Điện năng tiêu thụ": (5, 100)},
        transform=_t_may_giat,
    ),
    "Máy sấy quần áo": SheetRules(transform=_t_may_say),
    "Máy rửa chén": SheetRules(transform=_t_may_rua_chen),
    "Tủ mát, tủ đông": SheetRules(
        mandatory_khong=frozenset({"Dung tích tổng"}),
        transform=_t_tu_mat,
    ),
    "Máy nước nóng": SheetRules(
        drop=frozenset({"Chất liệu thân vỏ"}),
        transform=_t_may_nuoc_nong,
    ),
    "Micro karaoke": SheetRules(transform=_t_micro_karaoke),
    "Micro thu âm điện thoại": SheetRules(transform=_t_micro_thu_am),
    "Đồng hồ thông minh": SheetRules(
        drop=frozenset({"Thiết kế", "Đường kính"}),
        transform=_t_dong_ho,
    ),
    "Máy tính để bàn": SheetRules(transform=_t_may_tinh_ban),
    "Màn hình máy tính": SheetRules(transform=_t_man_hinh),
    "Máy in": SheetRules(transform=_t_may_in),
    "Máy tính bảng": SheetRules(
        drop=frozenset({"Độ phân giải"}),
        transform=_t_may_tinh_bang,
    ),
}


def clean_sheet(sheet_name: str, rows: list[dict]) -> tuple[list[dict], Counter]:
    """Làm sạch toàn bộ dòng của một sheet theo quy tắc chung + quy tắc riêng."""
    rules = SHEET_RULES.get(sheet_name, SheetRules())
    report: Counter = Counter()
    cleaned: list[dict] = []
    for raw in rows:
        r: dict = {}
        for k, v in raw.items():
            if k in rules.drop:
                report[f"drop.{k}"] += 0  # cột bị loại — đếm một lần bên dưới
                continue
            c = clean_cell(v)
            if c is None and v is not None and not _is_nan(v) \
                    and str(v).strip().lower() in PLACEHOLDERS:
                report["placeholder"] += 1
            r[k] = c
        for col in rules.mandatory_khong:
            if _is_neg(r.get(col)):
                r[col] = None
                report["khong_o_cot_bat_buoc"] += 1
        for col in rules.binary:
            _binary(r, col)
        cleaned.append(r)
    for col, (lo, hi) in rules.windows.items():
        for r in cleaned:
            _apply_window(r, col, lo, hi, report, f"ngoai_cua_so.{col}")
    if rules.transform:
        rules.transform(cleaned, report)
    for col in rules.drop:
        report[f"drop.{col}"] = len(rows)
    return cleaned, report
