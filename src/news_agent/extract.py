"""Bóc tách bài báo từ HTML.

Mỗi trường: `selectors` của nguồn trong sources.json → JSON-LD NewsArticle → thẻ meta (OpenGraph)
→ trafilatura (nội dung). Báo không khai báo selector vẫn chạy nhờ ba nguồn sau.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import trafilatura
from parsel import Selector

from news_agent.config import Selectors
from news_agent.models import Article
from news_agent.text import canonicalize_url, html_to_text, normalize_line, parse_date, same_site

_ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle"}
_ALWAYS_REMOVE = "script, style, noscript, iframe, form, button, svg, template"


class ExtractError(Exception):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(f"thiếu {', '.join(missing)}")


def extract_article(
    html: str,
    url: str,
    *,
    source_id: str,
    rules: Selectors,
    crawled_at: datetime,
    fallback_published_at: datetime | None = None,
) -> Article:
    """Raises ExtractError nếu thiếu tiêu đề, nội dung hoặc ngày đăng."""
    page = Selector(text=html)
    ld = _json_ld(page)
    meta = _meta(page)

    # Đọc mọi trường TRƯỚC khi dọn thân bài: tác giả, sapo của VnExpress nằm trong thân bài.
    title = normalize_line(
        _text(page, rules.title) or _s(ld.get("headline")) or meta.get("og:title")
    )
    sapo = normalize_line(
        _text(page, rules.sapo) or _s(ld.get("description")) or meta.get("og:description")
    )
    author = normalize_line(_text(page, rules.author) or _ld_author(ld)) or None
    category = normalize_line(
        _text(page, rules.category) or _s(ld.get("articleSection")) or meta.get("article:section")
    )
    content = html_to_text(_content_html(page, rules, html))
    published_at = (
        parse_date(_s(ld.get("datePublished")) or meta.get("article:published_time"))
        or parse_date(_text(page, rules.published))
        or fallback_published_at
    )
    missing = [name for name, value in [("title", title), ("content", content)] if not value]
    if published_at is None:
        missing.append("published_at")
    if missing or published_at is None:
        raise ExtractError(missing)

    canonical = page.css("link[rel=canonical]::attr(href)").get() or meta.get("og:url")
    # Chỉ tin canonical cùng site: bài đăng lại có thể trỏ canonical sang báo gốc.
    if not canonical or not same_site(urljoin(url, canonical), url):
        canonical = url
    image = meta.get("og:image") or _ld_image(ld.get("image"))
    return Article(
        source_id=source_id,
        url=url,
        canonical_url=canonicalize_url(canonical, url) or url,
        title=title,
        sapo=sapo,
        content=content,
        author=author,
        source_category=category or None,
        image_url=urljoin(url, image) if image else None,
        published_at=published_at,
        crawled_at=crawled_at,
        content_hash=hashlib.sha256(" ".join(content.casefold().split()).encode()).hexdigest(),
    )


def _content_html(page: Selector, rules: Selectors, html: str) -> str | None:
    if rules.content and (node := page.css(rules.content)):
        body = node[0]
        # Tiêu đề/sapo đôi khi nằm trong khối thân bài (VnExpress): bỏ để nội dung không lặp.
        junk = [_ALWAYS_REMOVE, *rules.remove, *filter(None, (rules.title, rules.sapo))]
        for node_ in body.css(", ".join(junk)):
            node_.drop()
        if body.xpath("normalize-space(string())").get():
            return str(body.get())
    # Báo đổi giao diện hoặc chưa có selector: trafilatura tìm phần nội dung chính.
    return trafilatura.extract(html, output_format="html", favor_precision=True)


def _text(page: Selector, css: str | None) -> str | None:
    if not css or not (node := page.css(css)):
        return None
    return " ".join(" ".join(node[0].css("::text").getall()).split()) or None


def _meta(page: Selector) -> dict[str, str]:
    result: dict[str, str] = {}
    for m in page.css("meta[content]"):
        key = m.attrib.get("property") or m.attrib.get("name") or m.attrib.get("itemprop")
        if key and (value := m.attrib["content"].strip()):
            result.setdefault(key.strip(), value)
    return result


def _json_ld(page: Selector) -> dict[str, Any]:
    for raw in page.css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue  # JSON-LD hỏng là chuyện thường gặp
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") in _ARTICLE_TYPES:
                return item
    return {}


def _ld_author(ld: dict[str, Any]) -> str | None:
    authors = ld.get("author")
    authors = authors if isinstance(authors, list) else [authors]
    # Chỉ lấy Person: "VnExpress", "TUOI TRE ONLINE" là tổ chức.
    names = [
        _s(a.get("name")) for a in authors if isinstance(a, dict) and a.get("@type") == "Person"
    ]
    return ", ".join(n for n in names if n) or None


def _ld_image(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url")
    return _s(value)


def _s(value: Any) -> str | None:
    return (" ".join(value.split()) or None) if isinstance(value, str) else None
