from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
from app.schemas import AdviceResult, FactCard
from app.agent_core.presenters import build_reco_card
from app.agent_core.retriever import get_catalog_metadata, get_category_price_floor
from app.agent_core.sales import closing_hook
from app.advice.provenance import facts_for_llm, format_vnd
from app.advice.verify import allowed_numbers, line_is_grounded, verify_advice, is_grounded

log = logging.getLogger("agent_core")

def _system_prompt(addr: str, self_term: str) -> str:
    return (
        "Bạn là chuyên gia tư vấn điện máy. NGUYÊN TẮC:\n"
        "1. CHỈ dùng đúng các con số xuất hiện trong FACTS (giá, thông số). TUYỆT ĐỐI không nêu bất kỳ "
        "con số nào KHÔNG có trong FACTS — không ước lượng tiền điện/tháng, không tự tính mức tiết kiệm, "
        "không đưa phần trăm/kWh/nghìn đồng suy diễn. Nếu muốn nói về lợi ích, hãy mô tả BẰNG LỜI, không kèm số.\n"
        "1b. Viết MỌI con số Y HỆT định dạng trong FACTS (ví dụ giá '10.010.000đ' phải giữ nguyên, "
        "KHÔNG đổi sang '10 triệu' hay '10,01 triệu'; thông số giữ nguyên đơn vị như FACTS).\n"
        "1c. KHÔNG cộng/gộp/tính tổng hay làm tròn các con số (ví dụ KHÔNG cộng dung tích ngăn đá + ngăn "
        "lạnh thành 'tổng ~330 lít'); chỉ nêu lại từng con số đúng như FACTS.\n"
        "2. Trình bày thông số bằng lợi ích thực tế (VD: Inverter -> tiết kiệm điện, RAM lớn -> đa nhiệm mượt, ...) "
        "nhưng không gắn con số tự bịa.\n"
        "3. UI đã tự hiển thị thẻ sản phẩm đầy đủ ở dưới. Không liệt kê lại toàn bộ thông số; "
        "chỉ giới thiệu tên mẫu và các điểm nổi bật liên quan trực tiếp tới nhu cầu. Khi khách yêu cầu "
        "so sánh, phân tích đánh đổi (trade-off) ngắn gọn, rõ ràng giữa các lựa chọn.\n"
        "4. Nếu trạng thái là budget_fallback: nói rõ không có sản phẩm trong ngân sách đó, rồi giới thiệu "
        "các mẫu giá gần nhất và ưu điểm để khách cân nhắc tăng ngân sách.\n"
        f"4b. Nếu trạng thái là price_spread: khách nhờ chọn giúp và chưa chốt ngân sách — nói rõ {self_term} chọn "
        f"đại diện 3 tầm giá (tiết kiệm / tầm trung / cao cấp) để {addr} dễ định hình, rồi giới thiệu từng mức.\n"
        "4c. Nếu trạng thái là custom_query: danh sách đã lọc đúng theo ràng buộc thông số khách nêu — "
        "nêu bật thông số đáp ứng ràng buộc đó (chỉ dùng số trong FACTS).\n"
        "4d. Nếu kết quả trả về KHÔNG KHỚP HOÀN TOÀN với yêu cầu của khách (ví dụ: vượt ngân sách, khác thương hiệu, thiếu tính năng), BẮT BUỘC phải nói rõ sự sai lệch này (VD: 'Mẫu này vượt ngân sách một chút', 'Mẫu này không hỗ trợ tính năng X'). TUYỆT ĐỐI không tự bịa tính năng để ép cho khớp.\n"
        "4e. ĐẶC BIỆT NHẤN MẠNH vào các tính năng mà khách đã yêu cầu (VD: khách cần 'nghe gọi', phải chỉ rõ mẫu nào có khả năng nghe gọi, mẫu nào không dựa vào phần FACTS).\n"
        "4f. Nếu không tìm thấy sản phẩm nào (FACTS trống rỗng), hãy lịch sự xin lỗi khách, giải thích lý do "
        "(dựa trên yêu cầu của khách không có trong dữ liệu) rồi đóng vai một người sale chuyên nghiệp: đặt giả "
        "định về nhu cầu thực của khách (VD: khách có thể đang muốn tìm phương án rẻ hơn), gợi mở một hướng đi "
        "khác (nới ngân sách, đổi loại sản phẩm khác trong tầm giá) và kết thúc bằng một câu hỏi cụ thể để làm rõ "
        "nhu cầu tiếp theo. TUYỆT ĐỐI KHÔNG đề xuất máy móc khi FACTS trống, và KHÔNG tự bịa số liệu thị trường "
        "không có trong FACTS.\n"
        "5. Giọng tự nhiên, chuyên nghiệp, mạch lạc, súc tích như người thật đang chat.\n"
        f"5b. Xưng '{self_term}' và gọi khách là '{addr}' xuyên suốt câu trả lời (không dùng 'bạn').\n"
        "6. KHÔNG dùng định dạng Markdown (tuyệt đối không dùng dấu sao `*` hoặc `#` để in đậm/in nghiêng). Để tạo danh sách, hãy xuống dòng và dùng dấu `-` hoặc số `1.` bình thường."
    )


def build_cards(rows: List[Dict[str, Any]], priority_features: List[str],
                self_term: str = "em") -> List[FactCard]:
    return [build_reco_card(r, priority_features, self_term=self_term) for r in rows]


def deterministic_message(intent: Dict[str, Any], status: str, db_path: Optional[str] = None,
                          addr: str = "anh/chị") -> Optional[str]:
    """Copy tất định cho meta_inquiry / no_products_found; None nếu cần LLM."""
    if status == "meta_inquiry":
        meta = get_catalog_metadata(db_path)
        cats = ", ".join(f"**{c}**" for c in meta["categories"])
        return (f"Chào {addr}, hệ thống hiện có **{len(meta['categories'])} danh mục** chính:\n\n{cats}\n\n"
                f"{addr.capitalize()} quan tâm danh mục nào, ngân sách và tính năng ra sao ạ?")
    if status == "no_products_found":
        category = intent.get("category")
        budget_max = intent.get("budget_max")
        floor = get_category_price_floor(category, db_path) if category and budget_max else None

        if floor and budget_max and floor > budget_max:
            cat_txt = f"{category} " if category else ""
            return (
                f"Dạ em xin lỗi {addr}, hiện trong dữ liệu của em không có sản phẩm {cat_txt}nào ở mức giá "
                f"{format_vnd(int(budget_max))} ạ. Trên thực tế, các mẫu {cat_txt}chính hãng hiện có giá khởi "
                f"điểm từ khoảng {format_vnd(int(floor))} trở lên.\n\n"
                f"Em giả định {addr} đang muốn tìm phương án rẻ hơn so với mức giá trước đó. Nếu {addr} có thể "
                f"nới ngân sách lên một chút, em có thể gợi ý một số mẫu giá phải chăng hơn. Hoặc nếu {addr} "
                f"đang tìm một sản phẩm khác trong tầm giá này, em cũng sẵn sàng hỗ trợ tư vấn ạ.\n\n"
                f"{addr.capitalize()} có thể cho em biết thêm nhu cầu cụ thể hơn không ạ? Ví dụ như loại sản "
                f"phẩm, các tính năng cần thiết? Em sẽ cố gắng tìm phương án phù hợp nhất trong khả năng dữ "
                f"liệu của mình."
            )
        return (f"Dạ em không tìm thấy sản phẩm nào khớp đầy đủ yêu cầu này trong dữ liệu hiện có ạ. "
                f"Em sẽ không gợi ý sang sản phẩm khác loại khi chưa có xác nhận của {addr}. "
                f"{addr.capitalize()} muốn đổi tiêu chí nào để em tìm lại không ạ?")
    return None


def generate_value_comparison_sentence(rows: List[Dict[str, Any]], priority_features: List[str],
                                       addr: str = "anh/chị") -> Tuple[str | None, float | None]:
    """
    Sinh câu so sánh giá trị:
    'Với thêm X đồng, {addr} được thêm [tính năng khách ưu tiên] ở mẫu Y'
    Trả về (câu_so_sánh, số_tiền_chênh_lệch_để_cho_phép).
    """
    from app.agent_core.presenters import load_specs, _price_value, product_display_name
    
    if not priority_features or len(rows) < 2:
        return None, None

    # Sắp xếp các sản phẩm theo giá tăng dần
    sorted_rows = []
    for r in rows:
        p = _price_value(r)
        if p > 0:
            sorted_rows.append((p, r))
    
    if len(sorted_rows) < 2:
        return None, None
        
    sorted_rows.sort(key=lambda x: x[0])
    
    # Lấy mẫu rẻ nhất p1
    p1_price, p1 = sorted_rows[0]
    p1_specs = load_specs(p1)
    p1_specs_lower = {k.lower(): v.lower() for k, v in p1_specs.items()}
    
    best_p2 = None
    best_features = []
    
    for p2_price, p2 in sorted_rows[1:]:
        p2_specs = load_specs(p2)
        p2_specs_lower = {k.lower(): v.lower() for k, v in p2_specs.items()}
        
        # Tìm các tính năng ưu tiên mà p2 có nhưng p1 không có
        matched = []
        for f in priority_features:
            f_low = f.lower()
            has_in_p2 = any(f_low in k or f_low in v for k, v in p2_specs_lower.items())
            has_in_p1 = any(f_low in k or f_low in v for k, v in p1_specs_lower.items())
            if has_in_p2 and not has_in_p1:
                matched.append(f)
        
        if matched:
            best_p2 = (p2_price, p2)
            best_features = matched
            break
            
    # Nếu không tìm thấy mẫu nào nổi trội hơn hẳn về tính năng ưu tiên, 
    # chọn luôn mẫu có giá cao tiếp theo và lấy tính năng ưu tiên đầu tiên mà nó có
    if not best_p2:
        p2_price, p2 = sorted_rows[1]
        p2_specs = load_specs(p2)
        p2_specs_lower = {k.lower(): v.lower() for k, v in p2_specs.items()}
        
        matched = []
        for f in priority_features:
            f_low = f.lower()
            if any(f_low in k or f_low in v for k, v in p2_specs_lower.items()):
                matched.append(f)
        
        if matched:
            best_p2 = (p2_price, p2)
            best_features = matched
        else:
            best_p2 = (p2_price, p2)
            best_features = [priority_features[0]]
            
    p2_price, p2 = best_p2
    price_diff = p2_price - p1_price
    if price_diff <= 0:
        return None, None
        
    diff_str = format_vnd(int(price_diff))
    features_str = ", ".join(best_features)
    p2_name = product_display_name(p2)
    
    sentence = f"Với thêm {diff_str}, {addr} được thêm {features_str} ở mẫu {p2_name}."
    return sentence, price_diff


def generate_advisor(query: str, intent: Dict[str, Any], rows: List[Dict[str, Any]],
                     status: str, llm, cards: List[FactCard],
                     on_delta: Optional[Callable[[str], None]] = None,
                     addr: str = "anh/chị", self_term: str = "em",
                     db_path: Optional[str] = None) -> Tuple[str, bool, List[str]]:
    """Sinh tư vấn top-3 + trade-off. Trả (message, streamed, warnings). Fail-closed nếu bịa số."""
    det = deterministic_message(intent, status, db_path, addr=addr)
    if det is not None:
        log.info("advisor: dùng văn mẫu tất định (status=%s), không gọi LLM", status)
        return det, False, []

    if status == "relaxed_preferences":
        features = ", ".join(intent.get("relaxed_features") or []) or "một số tính năng ưu tiên"
        warning = (f"Dạ, {self_term} vẫn tìm được các lựa chọn đúng nhóm sản phẩm và ngân sách. "
                   f"Tuy nhiên catalog chưa xác nhận đầy đủ các tính năng: {features}; "
                   f"vì vậy {self_term} chỉ xếp đây là các lựa chọn gần nhất, không khẳng định máy có các tính năng đó.")
        return warning + "\n\n" + _safe_summary(cards, self_term), False, []

    facts = facts_for_llm(cards)
    assump = [a for a in (intent.get("assumptions") or []) if a]
    assump_txt = (f"Giả định của {self_term} (phải nói rõ với khách rằng đây là {self_term} đang giả định): "
                  f"{'; '.join(assump)}\n" if assump else "")
    transition = intent.get("transition_message")
    trans_txt = f"LỜI CHUYỂN TIẾP (BẮT BUỘC dùng câu này làm câu mở đầu để giải thích sự suy luận): {transition}\n" if transition else ""
    
    wants_comp = intent.get("wants_comparison", False)
    # Chỉ chốt sale ngay (gỡ rào cản giao/lắp đặt rồi mời đặt hàng) khi đây là một đề xuất
    # ngắn gọn 1-2 sản phẩm, không phải bảng so sánh trade-off nhiều lựa chọn.
    is_single_recommend = not wants_comp and len(cards) > 0
    if is_single_recommend:
        # Câu hỏi cuối (chốt) do code chèn thêm sau (closing_hook), KHÔNG để LLM tự hỏi
        # lại kiểu "xem thêm lựa chọn khác" — tránh trùng lặp CTA.
        action = "Hãy ĐỀ XUẤT NGẮN GỌN 1-2 sản phẩm phù hợp nhất (chỉ nêu 2-3 điểm nổi bật nhất, tuyệt đối không liệt kê toàn bộ thông số dài dòng)."
    else:
        action = "Khách đã yêu cầu so sánh. Hãy phân tích đánh đổi (trade-off) ngắn gọn giữa các lựa chọn để khách dễ ra quyết định."
        
    user = (f"Trạng thái tìm kiếm: {status}\nFACTS (chỉ dùng dữ kiện này, nhưng đừng liệt kê lại vì UI đã hiển thị):\n{facts}\n\n"
            f"Nhu cầu khách: {query}\n{assump_txt}{trans_txt}\nCHỈ THỊ: {action}")

    # Tính toán câu so sánh giá trị nếu khách hỏi theo ngân sách và có priority_features
    comp_sentence = None
    extra_allowed_nums = set()
    budget_max = intent.get("budget_max")
    priority_features = intent.get("priority_features", [])
    
    if (budget_max is not None or status == "budget_fallback") and priority_features and len(rows) >= 2:
        comp_sentence, price_diff = generate_value_comparison_sentence(rows, priority_features, addr=addr)
        if price_diff:
            from app.advice.verify import extract_numbers
            extra_allowed_nums.update(extract_numbers(format_vnd(int(price_diff))))

    closing_line = None
    if is_single_recommend:
        from app.advice.verify import extract_numbers
        primary = rows[0] if rows else None
        closing_line = closing_hook(
            primary.get("category") if primary else None,
            float((primary or {}).get("price_clean") or 0),
            addr=addr, self_term=self_term,
        )
        extra_allowed_nums.update(extract_numbers(closing_line))

    # Streaming: phát từng dòng đã verify (line-level fail-closed).
    system = _system_prompt(addr, self_term)
    if on_delta is not None:
        allowed = allowed_numbers(cards)
        if extra_allowed_nums:
            allowed.update(extra_allowed_nums)
        parts: List[str] = []
        buf = ""

        def push(line: str) -> None:
            if line:
                on_delta(line)

        try:
            for token in llm.stream_text(system, user):
                parts.append(token)
                buf += token
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    push(line + "\n")
            if buf:
                push(buf)
                
            if comp_sentence:
                push("\n\n" + comp_sentence)
                parts.append("\n\n" + comp_sentence)
            if closing_line:
                push("\n\n" + closing_line)
                parts.append("\n\n" + closing_line)
        except Exception:
            return _blocking(llm, system, user, cards, self_term, comp_sentence, extra_allowed_nums, closing_line)

        result = verify_advice(
            AdviceResult(message="".join(parts), cards=cards, assumptions=[], warnings=[]),
            extra_allowed=extra_allowed_nums
        )
        if not is_grounded(result):
            log.warning("advisor(stream): LLM vi phạm lỗi số liệu, cảnh báo (warnings=%s)", list(result.warnings))
            return result.message, True, list(result.warnings)
        log.info("advisor(stream): câu trả lời LLM grounded, phát đủ")
        return result.message, True, []

    return _blocking(llm, system, user, cards, self_term, comp_sentence, extra_allowed_nums, closing_line)


def _blocking(llm, system: str, user: str, cards: List[FactCard], self_term: str = "em",
              comp_sentence: Optional[str] = None,
              extra_allowed_nums: Optional[set[str]] = None, closing_line: Optional[str] = None) -> Tuple[str, bool, List[str]]:
    try:
        message = llm.complete_text(system, user)
    except Exception as e:
        log.warning("advisor: LLM lỗi (%s) -> safe summary", e)
        summary = _safe_summary(cards, self_term)
        if closing_line:
            summary = summary + "\n\n" + closing_line
        return summary, False, []

    if comp_sentence:
        message = message.strip() + "\n\n" + comp_sentence
    if closing_line:
        message = message.strip() + "\n\n" + closing_line

    result = verify_advice(
        AdviceResult(message=message, cards=cards, assumptions=[], warnings=[]),
        extra_allowed=extra_allowed_nums
    )
    if not is_grounded(result):
        log.warning("advisor: LLM vi phạm lỗi số liệu, cảnh báo (warnings=%s)", list(result.warnings))
        return _safe_summary(cards, self_term), False, list(result.warnings)
    log.info("advisor: câu trả lời LLM grounded")
    return result.message, False, []


def _safe_summary(cards: List[FactCard], self_term: str = "em") -> str:
    lines = [f"Dạ {self_term} gợi ý các máy sau (thông tin lấy trực tiếp từ catalog):"]
    for i, c in enumerate(cards, 1):
        price = next((l.value for l in c.lines if l.label == "Giá"), "chưa có dữ liệu")
        # Tiêu đề card có dạng "Vì sao {self_term} đề xuất {name}?" — self_term đổi theo khách.
        title = c.title.split(" đề xuất ", 1)[-1].rstrip("?") if " đề xuất " in c.title else c.title.rstrip("?")
        url = next((l.value for l in c.lines if l.label == "Link sản phẩm"), "")
        url_text = f" - Xem chi tiết: {url}" if url else ""
        lines.append(f"{i}. {title} — giá {price}.{url_text}")
    return "\n".join(lines)
