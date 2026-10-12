import re
import time
from urllib.parse import urlencode, urljoin, urlparse, urlunparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://javtiful.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}
CODE_RE = re.compile(r"\b[A-Z]{2,10}[-_ ]?\d{2,8}(?:[-_][A-Z0-9]{1,8})?\b", re.I)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _page_url(code, page):
    query = {"q": code}
    if page > 1:
        query["page"] = str(page)
    return BASE_URL + "/zh/search?" + urlencode(query)


def _canonical(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def _parse_page(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
    cards = soup.select(".front-video-card, .video-card, .video-item, section .card, .card")
    anchors = []
    for card in cards:
        anchors.extend(card.select('a[href*="/video/"]'))
    if not anchors:
        anchors = soup.select('a[href*="/video/"]')

    for anchor in anchors:
        href = anchor.get("href")
        if not href:
            continue
        post_url = urljoin(page_url, href)
        parsed = urlparse(post_url)
        if parsed.hostname not in {"javtiful.com", "www.javtiful.com"} or "/video/" not in parsed.path:
            continue
        canonical = _canonical(post_url)
        if canonical in seen:
            continue
        seen.add(canonical)
        card = anchor
        for parent in anchor.parents:
            if getattr(parent, "name", None) in {"article", "li"} or (
                getattr(parent, "name", None) == "div"
                and any("card" in str(c).lower() or "video" in str(c).lower()
                        for c in (parent.get("class") or []))
            ):
                card = parent
                break
        title_node = card.select_one(".front-video-title, .video-title, h2, h3, h4, [title]")
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
        title = title or _clean(anchor.get("title")) or _clean(anchor.get_text(" ", strip=True))
        if not title:
            title = _clean(card.get_text(" ", strip=True))
        if len(title) < 3:
            continue
        image = card.select_one("img")
        thumbnail = None
        if image:
            thumbnail = (
                image.get("data-front-lazy-src") or image.get("data-src")
                or image.get("data-lazy-src") or image.get("src")
            )
        if not thumbnail:
            meta = card.select_one("meta[property='og:image']")
            thumbnail = meta.get("content") if meta else None
        match = CODE_RE.search(title) or CODE_RE.search(parsed.path)
        code = re.sub(r"[-_ ]+", "-", match.group(0)).upper() if match else None
        results.append({
            "title": title[:500],
            "url": canonical,
            "thumbnail": urljoin(page_url, thumbnail) if thumbnail else None,
            "code": code,
        })

    max_page = page
    for link in soup.select("a[href*='page='], .front-pagination a, .pagination a"):
        href = link.get("href") or ""
        match = re.search(r"[?&]page=(\d+)", href, re.I)
        text = _clean(link.get_text(" ", strip=True))
        if match:
            max_page = max(max_page, int(match.group(1)))
        elif text.isdigit():
            max_page = max(max_page, int(text))
    return results, max_page


def search_videos(code, max_pages=5, max_results=50):
    code = _clean(code).upper()
    if not code or not CODE_RE.fullmatch(code):
        raise ValueError("Send a valid video code, for example STAR-765.")
    session = requests.Session()
    session.headers.update(HEADERS)
    all_results = []
    seen = set()
    pages_to_check = 1
    for page in range(1, min(max_pages, pages_to_check) + 1):
        url = _page_url(code, page)
        response = session.get(url, timeout=25)
        response.raise_for_status()
        if len(response.text) < 100:
            raise RuntimeError("Javtiful returned an empty search page.")
        items, max_page = _parse_page(response.text, response.url)
        pages_to_check = min(max_pages, max(pages_to_check, max_page))
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                all_results.append(item)
                if len(all_results) >= max_results:
                    return all_results
        if not items:
            break
        if page < pages_to_check:
            time.sleep(0.25)
    return all_results
