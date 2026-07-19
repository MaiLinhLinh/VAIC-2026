"""Tri thức chính sách cửa hàng (giờ mở cửa, đặt hàng, thanh toán, bảo hành, khiếu nại).

Nguồn: các file markdown đã biên tập trong app/data/policies/, mỗi mục `## ` là một chunk.
Retrieval nhẹ in-memory (token overlap trên chữ bỏ dấu); LLM soạn câu trả lời nhưng
mọi con số phải truy nguyên được về tài liệu — vi phạm thì trả nguyên văn chunk (fail-closed).
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.nlu.preprocess import strip_accents

log = logging.getLogger("agent_core")

_POLICY_DIR = Path(__file__).resolve().parents[1] / "data" / "policies"

# Bỏ các từ hội thoại không mang nghĩa retrieval. Trước đây "ti vi thì sao"
# có thể khớp chunk chứa "vi phạm ... thì" và lấy nhầm cả mục Xe đạp.
_STOPWORDS = {
    "a", "ai", "anh", "ban", "ben", "chi", "cho", "co", "con", "cua", "da",
    "do", "duoc", "em", "gi", "hoi", "khong", "kia", "la", "minh", "nao",
    "nay", "nhe", "nhi", "nhu", "oi", "phai", "sao", "the", "thi", "toi",
    "vi", "voi", "vay",
}

# Khi không có category hội thoại, query phải chứa ít nhất một tín hiệu nghiệp vụ
# mới được phép tìm policy. Tên sản phẩm đơn thuần không phải là tín hiệu policy.
_POLICY_ANCHORS = {
    "cod", "dat", "dia", "doi", "dong", "giao", "gio",
    "hanh", "hoan", "hotline", "khieu", "lap", "lien", "luu", "mo", "mua", "nai",
    "online", "phi", "quyen", "ship", "sinh", "sua", "thanh", "thap", "thu", "toan", "tong",
    "tra", "van", "chuyen", "lieu", "xoa",
}

def _system_prompt(addr: str, self_term: str) -> str:
    return (
        "Bạn là nhân viên chăm sóc khách hàng của cửa hàng điện máy. NGUYÊN TẮC:\n"
        "1. CHỈ trả lời dựa trên TÀI LIỆU được cung cấp. TUYỆT ĐỐI không bịa thông tin, "
        "không suy diễn chính sách không có trong tài liệu.\n"
        "2. Mọi con số (giờ giấc, số điện thoại, số ngày, số lượng) phải viết Y HỆT như trong tài liệu.\n"
        f"3. Nếu tài liệu không có thông tin khách hỏi, nói thật là {self_term} chưa có thông tin phần này "
        "và mời khách gọi tổng đài 1900.232.461 để được hỗ trợ.\n"
        "4. Trả lời ngắn gọn 2-4 câu, giọng lễ phép 'Dạ/ạ', đi thẳng vào ý khách hỏi.\n"
        "4b. Nếu bối cảnh cho biết khách đang hỏi về MỘT nhóm sản phẩm cụ thể (VD tủ lạnh), CHỈ nêu "
        "nội dung tài liệu áp dụng cho nhóm đó; TUYỆT ĐỐI không lấy ví dụ hay đơn giá của nhóm sản phẩm "
        "khác (khách hỏi tủ lạnh thì không nói về khung treo tivi hay ống đồng máy lạnh). Nếu tài liệu "
        "không có mục riêng cho nhóm đó, nêu quy định chung áp dụng cho nhóm hàng tương ứng "
        "(VD tủ lạnh thuộc nhóm hàng lắp đặt) và nói rõ đây là quy định chung.\n"
        "5. Kết thúc bằng một câu mời khách tiếp tục cho biết nhu cầu mua sắm nếu cần.\n"
        f"5b. Xưng '{self_term}' và gọi khách là '{addr}' xuyên suốt câu trả lời (không dùng 'bạn').\n"
        "6. KHÔNG dùng định dạng Markdown (không dấu sao hay thăng); danh sách thì xuống dòng dùng dấu '-'."
    )


def _flat(text: str) -> str:
    lowered = text.lower()
    # Phải bỏ từ hỏi trước khi strip accents; nếu không "mấy giờ" biến thành
    # "may gio" và đụng chính xác token "máy" trong "máy lạnh".
    lowered = re.sub(r"\bmấy\b", " ", lowered)
    flat = strip_accents(lowered)
    # Chuẩn hóa cách viết tách âm tiết phổ biến để không còn token rác "ti", "vi".
    return re.sub(r"\bti\s+vi\b", "tivi", flat)


def _tokens(text: str) -> List[str]:
    return [
        t for t in re.split(r"[^\w]+", _flat(text))
        if len(t) > 1 and t not in _STOPWORDS
    ]


def _has_policy_anchor(text: str) -> bool:
    tokens = set(_tokens(text))
    return bool(tokens & _POLICY_ANCHORS)


@lru_cache(maxsize=4)
def load_policy_chunks(policy_dir: Optional[str] = None) -> tuple:
    """Đọc mọi file .md trong thư mục chính sách, cắt chunk theo heading `## `."""
    base = Path(policy_dir) if policy_dir else _POLICY_DIR
    if not base.exists():
        log.warning("policy: thư mục tài liệu không tồn tại: %s", base)
        return tuple()
    chunks: List[Dict[str, Any]] = []
    for f in sorted(base.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("policy: không đọc được %s (%s)", f, e)
            continue
        for part in re.split(r"(?m)^##\s+", text)[1:]:
            title, _, body = part.partition("\n")
            body = body.strip()
            if body:
                chunks.append({"source": f.stem, "title": title.strip(), "text": body})
    log.info("policy: nạp %d chunk từ %s", len(chunks), base)
    return tuple(chunks)


def search_policy(query: str, top_k: int = 3, policy_dir: Optional[str] = None,
                  category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Chấm điểm chunk theo độ trùng token bỏ dấu (tiêu đề nhân đôi trọng số).
    category (nhóm hàng khách đang bàn) được trộn vào truy vấn để chunk nhắc tới
    nhóm đó thắng chunk chung chung."""
    chunks = load_policy_chunks(policy_dir)
    # Không có category và cũng không có từ nghiệp vụ => đây không phải một
    # truy vấn policy đủ mạnh. Fail closed thay vì lấy đại chunk có một từ chung.
    if not category and not _has_policy_anchor(query):
        return []

    q_tokens = set(_tokens(f"{query} {category or ''}"))
    if not chunks or not q_tokens:
        return []
    scored = []
    category_flat = _flat(category).strip() if category else ""
    for c in chunks:
        title_tokens = set(_tokens(c["title"]))
        body_tokens = set(_tokens(c["text"]))
        score = 2.0 * len(q_tokens & title_tokens) + 1.0 * len(q_tokens & body_tokens)
        if category_flat and category_flat in _flat(f"{c['title']} {c['text']}"):
            # Exact phrase phải áp đảo overlap một token như "lạnh" giữa
            # "Tủ lạnh" và "Máy lạnh".
            score += 20.0
        # Một token chỉ trùng trong thân bài là quá yếu; ít nhất phải trùng tiêu
        # đề (2 điểm), hai token thân bài, hoặc exact category (boost ở trên).
        if score >= 2.0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_k]]


def _numbers_grounded(reply: str, docs: str) -> bool:
    """Mọi cụm số trong câu trả lời phải xuất hiện (theo dạng chỉ-chữ-số) trong tài liệu."""
    doc_nums = {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d\.\,:h\-]*\d|\d", docs)}
    for m in re.findall(r"\d[\d\.\,:h\-]*\d|\d", reply):
        digits = re.sub(r"\D", "", m)
        if len(digits) <= 1:
            continue  # số đơn lẻ (đánh số danh sách) không coi là dữ kiện
        if digits not in doc_nums:
            log.warning("policy: câu trả lời chứa số lạ %r không có trong tài liệu", m)
            return False
    return True


def answer_policy(query: str, llm=None, policy_dir: Optional[str] = None,
                  history: Optional[List[Dict[str, str]]] = None,
                  category: Optional[str] = None, addr: str = "anh/chị",
                  self_term: str = "em") -> str:
    """Soạn câu trả lời chính sách THEO NGỮ CẢNH hội thoại (nhóm hàng khách đang bàn).
    LLM lỗi/bịa số -> trả nguyên văn chunk khớp nhất."""
    retrieval_query = query
    # Follow-up kiểu "tủ lạnh thì sao" cần mượn chủ đề policy gần nhất (vd giao
    # hàng) để xếp đúng chunk, nhưng chỉ khi category đã được catalog xác nhận.
    if category and not _has_policy_anchor(query):
        for message in reversed(history or []):
            if message.get("role") == "user" and _has_policy_anchor(message.get("content", "")):
                retrieval_query = f"{message['content']} {query}"
                break
    hits = search_policy(retrieval_query, top_k=3, policy_dir=policy_dir, category=category)
    if not hits:
        log.info("policy: không tìm thấy chunk khớp cho %r", query)
        return (f"Dạ phần này {self_term} chưa có thông tin chính xác ạ. {addr.capitalize()} vui lòng gọi tổng đài "
                "1900.232.461 (7:30 - 22:00 mỗi ngày) để được hỗ trợ chi tiết hơn. "
                f"Ngoài ra {addr} đang cần tìm sản phẩm nào để {self_term} tư vấn không ạ?")
    docs = "\n\n".join(f"[{h['title']}]\n{h['text']}" for h in hits)
    ctx = ""
    if category:
        ctx += f"BỐI CẢNH: khách đang được tư vấn nhóm sản phẩm '{category}' — trả lời cho đúng nhóm này.\n"
    recent = [m for m in (history or []) if m.get("content")][-6:]
    if recent:
        ctx += "Hội thoại gần nhất:\n" + "\n".join(
            f"{'Khách' if m.get('role') == 'user' else 'Trợ lý'}: {m['content'][:200]}" for m in recent) + "\n"
    if llm is not None:
        try:
            reply = (llm.complete_text(_system_prompt(addr, self_term),
                                       f"TÀI LIỆU:\n{docs}\n\n{ctx}Câu hỏi của khách: {query}") or "").strip()
            if reply and _numbers_grounded(reply, docs):
                log.info("policy: trả lời qua LLM grounded (chunks=%s)",
                         [h["title"] for h in hits])
                return reply
            log.warning("policy: LLM trả lời rỗng hoặc dính số lạ -> dùng nguyên văn tài liệu")
        except Exception as e:
            log.warning("policy: LLM lỗi (%s) -> dùng nguyên văn tài liệu", e)
    best = hits[0]
    return (f"Dạ về {best['title'].lower()}, bên {self_term} quy định như sau ạ:\n\n{best['text']}\n\n"
            f"{addr.capitalize()} cần {self_term} hỗ trợ thêm thông tin nào nữa không ạ?")
