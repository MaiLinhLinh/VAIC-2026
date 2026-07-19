import re
import json
import logging
import requests
from html import unescape

logger = logging.getLogger("crawler")

_CRAWL_CACHE = {}
_DETAIL_CACHE = {}

# Circuit breaker: trên prod, dienmayxanh.com thường chặn IP datacenter — sau
# N lần lỗi liên tiếp thì ngừng cào để không làm chậm response (mỗi lần timeout 5-8s).
_FAIL_STREAK = 0
_FAIL_LIMIT = 3


def _network_ok() -> bool:
    return _FAIL_STREAK < _FAIL_LIMIT


def _record_result(success: bool) -> None:
    global _FAIL_STREAK
    _FAIL_STREAK = 0 if success else _FAIL_STREAK + 1
    if _FAIL_STREAK == _FAIL_LIMIT:
        logger.warning("Crawler circuit breaker OPEN: %d consecutive failures, "
                       "skipping further live crawls", _FAIL_STREAK)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}
_LDJSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
# dienmayxanh có 2 vị trí đặt schema Product:
# 1. <script type="application/ld+json"> block (phổ biến: tu_lanh, may_say_quan_ao, ...)
# 2. <input id="jsonProductGTM" value="..."> HTML-encoded (phổ biến: man-hinh-may-tinh, ...)
_GTM_INPUT_RE = re.compile(r'<input[^>]*id="jsonProductGTM"[^>]*value="([^"]*)"', re.I)
_MAX_REVIEWS = 3

def fetch_product_info(product_id) -> tuple[str | None, str | None]:
    """
    Fetches the product link and image URL from Dien May Xanh viewed history API.
    Returns (product_link, image_url)
    """
    if not product_id:
        return None, None
        
    pid = str(product_id).strip()
    if pid.endswith('.0'):
        pid = pid[:-2]
        
    if not pid or pid.lower() in ('nan', 'none', 'null', ''):
        return None, None
        
    if pid in _CRAWL_CACHE:
        return _CRAWL_CACHE[pid]

    if not _network_ok():
        return None, None

    url = "https://www.dienmayxanh.com/Ajax/GetViewedHistory"
    data = {
        'productIds[]': pid
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
    }
    
    try:
        logger.info(f"Crawling Dien May Xanh for productId: {pid}")
        response = requests.post(url, data=data, headers=headers, timeout=5)
        if response.status_code == 200:
            html = response.text
            
            # Extract href
            href_match = re.search(r'<a\s+href=["\']([^"\']+)["\']', html)
            link = None
            if href_match:
                link = href_match.group(1)
                # Unescape HTML entities
                link = link.replace('&amp;', '&')
                if link.startswith('/'):
                    link = "https://www.dienmayxanh.com" + link
                    
            # Extract img src
            img_match = re.search(r'<img\s+[^>]*src=["\']([^"\']+)["\']', html)
            image = None
            if img_match:
                image = img_match.group(1)
                if image.startswith('//'):
                    image = "https:" + image
                    
            logger.info(f"Crawled product_id {pid}: link={link}, img={image}")
            _CRAWL_CACHE[pid] = (link, image)
            _record_result(True)
            return link, image
        _record_result(False)
    except Exception as e:
        logger.error(f"Error crawling product_id {pid}: {e}")
        _record_result(False)

    _CRAWL_CACHE[pid] = (None, None)
    return None, None


def _parse_product_ldjson(html: str) -> dict | None:
    """Tìm block JSON-LD @type=Product trong HTML trang sản phẩm.

    Thử cả hai vị trí đặt schema của dienmayxanh; trả về Product đầu tiên tìm được.
    """
    # Method 1: <script type="application/ld+json">
    for m in _LDJSON_RE.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    # Method 2: <input id="jsonProductGTM"> (HTML-encoded JSON)
    m = _GTM_INPUT_RE.search(html)
    if m:
        try:
            data = json.loads(unescape(m.group(1)))
        except (ValueError, TypeError):
            pass
        else:
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
    return None


def _extract_stock(product: dict) -> str | None:
    availability = str((product.get("offers") or {}).get("availability") or "")
    if "InStock" in availability:
        return "Còn hàng"
    if "OutOfStock" in availability or "Discontinued" in availability:
        return "Hết hàng"
    return None


def _extract_rating(product: dict) -> tuple[float | None, int | None]:
    agg = product.get("aggregateRating") or {}
    rating, count = None, None
    try:
        if agg.get("ratingValue") is not None:
            rating = float(agg["ratingValue"])
        raw_count = agg.get("reviewCount", agg.get("reviewcount"))
        if raw_count is not None:
            count = int(raw_count)
    except (ValueError, TypeError):
        pass
    return rating, count


def _extract_reviews(product: dict) -> list[dict]:
    reviews = []
    for r in (product.get("review") or [])[:_MAX_REVIEWS]:
        if not isinstance(r, dict):
            continue
        body = str(r.get("reviewBody") or "").strip()
        if not body:
            continue
        author = (r.get("author") or {}).get("name")
        rating = None
        try:
            rv = (r.get("reviewRating") or {}).get("ratingValue")
            if rv is not None:
                rating = float(rv)
        except (ValueError, TypeError):
            pass
        reviews.append({"author": author, "rating": rating, "content": body})
    return reviews


def _extract_installment(html: str) -> str | None:
    if 'data-installment="1"' not in html and "/tra-gop/" not in html:
        return None
    if re.search(r'tr[ảa] g[óo]p 0%', html, re.I):
        return "Có hỗ trợ trả góp 0% lãi suất"
    return "Có hỗ trợ trả góp"


def fetch_product_detail(product_link: str | None) -> dict | None:
    """
    Cào trang sản phẩm Điện Máy Xanh để lấy dữ liệu bổ sung:
    tồn kho (offers.availability), đánh giá (aggregateRating + review) và trả góp.
    Trả về dict {stock_status, rating, review_count, reviews, installment} hoặc None nếu lỗi.
    """
    if not product_link or "dienmayxanh.com" not in product_link:
        return None
    if product_link in _DETAIL_CACHE:
        return _DETAIL_CACHE[product_link]

    if not _network_ok():
        return None

    try:
        logger.info(f"Crawling product detail: {product_link}")
        response = requests.get(product_link, headers=_HEADERS, timeout=8)
        if response.status_code != 200:
            logger.warning(f"Detail crawl failed ({response.status_code}): {product_link}")
            _record_result(False)
            _DETAIL_CACHE[product_link] = None
            return None
        html = response.text
        product = _parse_product_ldjson(html)
        # Installment không phụ thuộc JSON-LD, cào luôn để không mất dữ liệu.
        installment = _extract_installment(html)
        if product is None:
            logger.debug(f"No Product JSON-LD: {product_link}")
            _record_result(True)  # mạng thông, chỉ là trang không có schema
            if installment:
                detail = {
                    "stock_status": None,
                    "rating": None,
                    "review_count": None,
                    "reviews": [],
                    "installment": installment,
                }
                _DETAIL_CACHE[product_link] = detail
                return detail
            _DETAIL_CACHE[product_link] = None
            return None

        rating, review_count = _extract_rating(product)
        detail = {
            "stock_status": _extract_stock(product),
            "rating": rating,
            "review_count": review_count,
            "reviews": _extract_reviews(product),
            "installment": installment,
        }
        logger.info(f"Detail crawled: stock={detail['stock_status']}, rating={rating}, "
                    f"reviews={review_count}, installment={detail['installment']}")
        _record_result(True)
        _DETAIL_CACHE[product_link] = detail
        return detail
    except Exception as e:
        logger.error(f"Error crawling detail {product_link}: {e}")
        _record_result(False)
        _DETAIL_CACHE[product_link] = None
        return None


def enrich_card_with_detail(card) -> None:
    """
    Bổ sung dữ liệu cào được (tồn kho / đánh giá / trả góp) vào FactCard:
    set field cấu trúc cho UI, thêm FactLine cho LLM, và bỏ mục tương ứng khỏi 'missing'.
    """
    from app.schemas import FactLine, ReviewItem

    detail = fetch_product_detail(card.product_link)
    if not detail:
        return

    src = "dienmayxanh.com"
    found: set[str] = set()

    if detail["stock_status"]:
        card.stock_status = detail["stock_status"]
        card.lines.append(FactLine(label="Tình trạng", value=detail["stock_status"], source=src))
        found.add("tồn kho")

    if detail["rating"] is not None:
        card.rating = detail["rating"]
        card.review_count = detail["review_count"]
        count_txt = f" ({detail['review_count']} đánh giá)" if detail["review_count"] else ""
        card.lines.append(FactLine(label="Đánh giá", value=f"{detail['rating']}/5{count_txt}", source=src))
        found.add("đánh giá người dùng (review)")

    if detail["reviews"]:
        card.reviews = [ReviewItem(**r) for r in detail["reviews"]]
        found.add("đánh giá người dùng (review)")

    if detail["installment"]:
        card.installment = detail["installment"]
        card.lines.append(FactLine(label="Trả góp", value=detail["installment"], source=src))
        found.add("trả góp")

    card.missing = [m for m in card.missing if m not in found]
