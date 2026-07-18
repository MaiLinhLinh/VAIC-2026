"""Test cho engine làm sạch spec sheet (clean_rules).

Mỗi quyết định làm sạch đã chốt (CONTEXT.md) có ít nhất một test bám vào
ca dữ liệu thật đã khảo sát.
"""
from app.catalog.clean_rules import clean_cell, clean_sheet


def _one(sheet: str, row: dict) -> tuple[dict, dict]:
    rows, report = clean_sheet(sheet, [row])
    return rows[0], report


# ---------------------------------------------------------------- toàn cục

def test_placeholder_ve_chua_co_du_lieu():
    assert clean_cell("Đang cập nhật") is None
    assert clean_cell("Hãng không công bố") is None
    assert clean_cell("null") is None
    assert clean_cell("  ") is None


def test_strip_ky_tu_vo_hinh():
    # ca thật: "‎250-2000 trang/tháng" (sheet Máy in)
    assert clean_cell("‎250-2000 trang/tháng") == "250-2000 trang/tháng"
    assert clean_cell("a​b c") == "ab c"  # ZWSP xoá (zero-width), NBSP thành space


def test_khong_o_cot_bat_buoc_thanh_thieu_du_lieu():
    # "Dung tích tổng: Không" trong khi tủ nào cũng có dung tích -> thiếu dữ liệu
    r, rep = _one("Tủ Lạnh", {"Dung tích tổng": "Không", "Dung tích ngăn đá": "Không"})
    assert r["Dung tích tổng"] is None
    assert rep["khong_o_cot_bat_buoc"] == 1
    # cột tuỳ chọn (ngăn đá của tủ mini) giữ nguyên: vắng mặt thật
    assert r["Dung tích ngăn đá"] == "Không"


def test_cua_so_hop_ly_loai_gia_tri_rac():
    # ca thật: median 54026 "kWh/năm" của Tủ Lạnh
    r, rep = _one("Tủ Lạnh", {"Điện năng tiêu thụ": "54026"})
    assert r["Điện năng tiêu thụ"] is None
    assert r["Điện năng tiêu thụ (nguyên văn)"] == "54026"
    r, _ = _one("Tủ Lạnh", {"Điện năng tiêu thụ": "381"})
    assert r["Điện năng tiêu thụ"] == "381"


def test_wh_khong_ro_moc_thoi_gian_khong_suy_doan():
    r, rep = _one("Tủ Lạnh", {"Điện năng tiêu thụ": "128 - 152 Wh"})
    assert r["Điện năng tiêu thụ"] is None
    assert rep["tu_lanh.dien_nang_wh_khong_ro_moc"] == 1


# ---------------------------------------------------------------- theo sheet

def test_may_lanh_loai_cot_rac():
    r, _ = _one("Máy lạnh", {"Số lượng": "Khoảng 7000 trang A4",
                             "Điện năng tiêu thụ": "1", "Loại Gas": "R-32"})
    assert "Số lượng" not in r and "Điện năng tiêu thụ" not in r
    assert r["Loại Gas"] == "R-32"


def test_do_on_lay_min_khoang_khong_phai_so_am():
    # "32-42 dB" là khoảng 32..42, không phải 32 và -42
    r, _ = _one("Máy lạnh", {"Độ ồn": "Dàn lạnh: 37 - 45 dB - Dàn nóng: 53 dB"})
    assert r["Độ ồn"] == "37 dB"
    r, _ = _one("Tủ mát, tủ đông", {"Độ ồn": "32-42 dB"})
    assert r["Độ ồn"] == "32 dB"
    # công suất lạc vào cột độ ồn -> ngoài cửa sổ 15-70 dB
    r, rep = _one("Tủ mát, tủ đông", {"Độ ồn": "180W"})
    assert r["Độ ồn"] is None
    assert rep["tu_mat.do_on_ngoai_cua_so"] == 1


def test_nhan_nang_luong_parse_kep():
    r, _ = _one("Máy lạnh", {"Nhãn năng lượng": "5 sao (Hiệu suất năng lượng 5.30)"})
    assert r["Nhãn năng lượng (sao)"] == "5"
    assert r["Hiệu suất năng lượng"] == "5.3"


def test_may_giat_mau_thuan_long_giat_chi_bao_cao():
    row = {"Lồng giặt": "Lồng đứng", "Loại sản phẩm": "Cửa trước"}
    r, rep = _one("Máy giặt", row)
    assert rep["may_giat.long_giat_mau_thuan_loai_sp"] == 1
    assert r["Lồng giặt"] == "Lồng đứng"      # không tự sửa
    assert r["Loại sản phẩm"] == "Cửa trước"


def test_may_say_dien_nang_la_cong_suat():
    r, _ = _one("Máy sấy quần áo", {"Điện năng tiêu thụ": "2250W"})
    assert r["Công suất"] == "2250W"


def test_may_rua_chen_so_luong_parse_kep():
    r, _ = _one("Máy rửa chén", {"Số lượng": "3 - 4 bữa ăn Việt (13 bộ Châu Âu)"})
    assert r["Số bộ chén"] == "13 bộ"
    assert r["Số bữa ăn Việt"] == "3 - 4 bữa"


def test_tu_mat_quy_doi_timebase_ve_don_vi_troi():
    rows, rep = clean_sheet("Tủ mát, tủ đông", [
        {"Điện năng tiêu thụ": "2.15 kWh/ngày"},
        {"Điện năng tiêu thụ": "1.2 kWh/ngày"},
        {"Điện năng tiêu thụ": "365 kWh/năm"},
    ])
    # kWh/ngày trội -> giá trị kWh/năm được quy đổi /365
    assert rows[2]["Điện năng tiêu thụ"] == "1 kwh/ngày"
    assert rows[0]["Điện năng tiêu thụ"] == "2.15 kWh/ngày"
    assert rep["tu_mat.dien_nang_quy_doi_timebase"] == 1


def test_tu_mat_chuan_hoa_nhiet_do():
    r, _ = _one("Tủ mát, tủ đông", {"Nhiệt độ ngăn đông (độ C)": "Dưới -18℃"})
    assert r["Nhiệt độ ngăn đông (độ C)"] == "≤ -18°C"


def test_may_nuoc_nong_binh_chua_parse_nguoi_va_binary():
    r, _ = _one("Máy nước nóng", {
        "Dung lượng dung tích": "30 lít (khoảng 3-5 người sử dụng)",
        "Bơm trợ lực": "Không có bơm trợ lực"})
    assert r["Số người sử dụng"] == "3-5 người"
    assert r["Bơm trợ lực"] == "Không có"


def test_micro_karaoke_tach_tan_so():
    r, _ = _one("Micro karaoke", {"Tần số hoạt động": "640 - 690 MHz"})
    assert r["Băng tần RF"] == "640 - 690 MHz"
    # "70 - 15 kHz" giảm dần vô lý -> số đầu là Hz bị cắt đơn vị
    r, rep = _one("Micro karaoke", {"Tần số hoạt động": "70 - 15 kHz"})
    assert r["Dải tần âm thanh"] == "70 Hz - 15 kHz"
    assert rep["micro_karaoke.tan_so_suy_don_vi_hz"] == 1


def test_micro_thu_am_giu_nguyen_gia_tri_lech_cot():
    r, rep = _one("Micro thu âm điện thoại", {"Nhiệt độ hoạt động bộ phát": "2024"})
    assert r["Nhiệt độ hoạt động bộ phát"] == "2024"   # giữ nguyên (Q14.3)
    assert rep["micro_thu_am.gia_tri_lech_cot"] == 1


def test_dong_ho_chu_vi_nhiem_model_code_va_atm():
    r, rep = _one("Đồng hồ thông minh", {
        "Chu vi cổ tay": "191134",
        "Chuẩn chống nước, bụi": "Chống nước 5 ATM - ISO 22810:2010 (Tắm, bơi)"})
    assert r["Chu vi cổ tay"] is None
    assert rep["dong_ho.chu_vi_nhiem_model_code"] == 1
    assert r["Chống nước (ATM)"] == "5 ATM"
    r, _ = _one("Đồng hồ thông minh", {"Chu vi cổ tay": "14 - 21 cm"})
    assert r["Chu vi cổ tay"] == "14 - 21 cm"


def test_may_tinh_ban_may_bo_lap_rap_khong_phai_linh_kien():
    # máy bộ Rosa/Singpc ghi kèm case/mainboard nhưng có đủ CPU/RAM/OS
    r, _ = _one("Máy tính để bàn", {
        "Công nghệ CPU": "AMD Ryzen 3000 Series", "RAM": "16 GB",
        "Hỗ trợ mainboard": "Micro-ATX | Mini-ITX", "Loại Case": "SFF"})
    assert r["Loại hàng"] == "máy hoàn chỉnh"
    r, rep = _one("Máy tính để bàn", {"Model GPU": "DUAL-RTX3050-6G",
                                      "Bộ nguồn đề xuất": "550W"})
    assert r["Loại hàng"] == "linh kiện (card đồ hoạ)"
    assert rep["may_tinh_ban.linh_kien"] == 1


def test_may_tinh_ban_toc_do_cpu_khong_nhan_bang_thong():
    r, rep = _one("Máy tính để bàn", {"Tốc độ CPU": "120 GB/s memory bandwidth"})
    assert r["Tốc độ CPU"] is None
    assert r["Tốc độ CPU (nguyên văn)"] == "120 GB/s memory bandwidth"
    r, _ = _one("Máy tính để bàn", {"Tốc độ CPU": "2.50 GHz"})
    assert r["Tốc độ CPU"] == "2.50 GHz"


def test_man_hinh_so_mau_va_tuong_phan():
    r, rep = _one("Màn hình máy tính", {"Số lượng": "16.7 triệu màu",
                                        "Độ tương phản tĩnh": "1000:2.1"})
    assert r["Số màu hiển thị"] == "16.7 triệu màu"
    assert r["Độ tương phản tĩnh"] is None
    assert rep["man_hinh.tuong_phan_sai_khuon"] == 1
    r, _ = _one("Màn hình máy tính", {"Độ tương phản tĩnh": "1000:1"})
    assert r["Độ tương phản tĩnh"] == "1000:1"


def test_may_in_relabel_va_tach_toc_do():
    r, _ = _one("Máy in", {
        "Phụ kiện đi kèm": "Khoảng 2600 trang",
        "Kích thước phụ kiện": "A4 | B5 | A6",
        "Tốc độ in": "33 trang/phút (Đen trắng) - 15 trang/phút (Màu)"})
    assert r["Hiệu suất mực đi kèm"] == "Khoảng 2600 trang"
    assert r["Khổ giấy hỗ trợ"] == "A4 | B5 | A6"
    assert r["Tốc độ in đen trắng"] == "33 trang/phút"
    assert r["Tốc độ in màu"] == "15 trang/phút"
    # giá trị trần không nhãn: giữ là tốc độ chung, không đoán chế độ
    r, _ = _one("Máy in", {"Tốc độ in": "20 trang/phút"})
    assert "Tốc độ in đen trắng" not in r
    assert r["Tốc độ in"] == "20 trang/phút"


def test_may_tinh_bang_pin_song_song_sac_va_brand():
    r, _ = _one("Máy tính bảng", {
        "brand": "Ipad (Apple)", "Dung lượng pin": "28.93 Wh",
        "Phụ kiện đi kèm": "20 W", "Dung lượng khả dụng": "241",
        "Chip xử lý (CPU)": "null"})
    assert r["brand"] == "Apple"
    assert r["Pin (Wh)"] == "28.93 Wh"
    assert "Pin (mAh)" not in r
    assert r["Sạc kèm theo"] == "20 W"
    assert r["Dung lượng khả dụng"] == "241 GB"
    assert r["Chip xử lý (CPU)"] is None          # "null" là placeholder
    r, _ = _one("Máy tính bảng", {"Dung lượng pin": "8000 mAh"})
    assert r["Pin (mAh)"] == "8000 mAh"
