"""Tự tìm link bài của một báo, không cần khai báo: RSS, Google News sitemap, trang chủ.

Hàm thuần nhận nội dung đã tải; spider lo việc tải.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from lxml import etree
from parsel import Selector

from news_agent.models import Link
from news_agent.text import canonicalize_url, parse_date, same_site

# Sitemap index thường trỏ cả sitemap chuyên mục/ảnh/video: chỉ theo sitemap "tin tức".
_NEWS_SITEMAP = re.compile(r"news", re.I)
# URL bài báo Việt kết thúc bằng mã số dài: ...-5137160.html, ...-185261027191016378.htm
ARTICLE_URL = re.compile(r"-\d{6,}\.html?$")
_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)


def news_sitemaps(robots_txt: str) -> list[str]:
    """Các dòng `Sitemap:` trong robots.txt có chữ "news" (Google News sitemap, latest-news...)."""
    urls = [
        line.split(":", 1)[1].strip()
        for line in robots_txt.splitlines()
        if line.lower().startswith("sitemap:")
    ]
    return [u for u in urls if _NEWS_SITEMAP.search(u)]


def feed_links(html: str, page_url: str) -> list[str]:
    """RSS khai báo trong <link rel=alternate> và mọi link *.rss cùng site (trang /rss của báo)."""
    page = Selector(text=html)
    hrefs = page.css(
        "link[rel=alternate][type*=rss]::attr(href), link[rel=alternate][type*=atom]::attr(href)"
    ).getall()
    hrefs += [h for h in page.css("a::attr(href)").getall() if h.split("?")[0].endswith(".rss")]
    urls = (urljoin(page_url, h.strip()) for h in hrefs)
    return list(dict.fromkeys(u for u in urls if same_site(u, page_url)))


def article_links(html: str, page_url: str) -> list[Link]:
    """Link bài trên một trang HTML (trang chủ): chưa biết ngày đăng."""
    hrefs = Selector(text=html).css("a::attr(href)").getall()
    urls = (canonicalize_url(h, page_url) for h in hrefs)
    return [
        Link(u)
        for u in dict.fromkeys(u for u in urls if u)
        if same_site(u, page_url) and ARTICLE_URL.search(urlsplit(u).path)
    ]


def parse_feed(body: bytes) -> tuple[list[Link], list[str]] | None:
    """RSS, Atom, sitemap urlset/sitemapindex → (link bài, sitemap con). Không phải XML → None."""
    try:
        root = etree.fromstring(body, _XML_PARSER)
    except etree.XMLSyntaxError:
        return None
    if root is None:
        return None
    tag = etree.QName(root).localname
    if tag == "rss" or tag == "feed":
        return _rss(root), []
    if tag == "urlset":
        return _urlset(root), []
    if tag == "sitemapindex":
        children = [
            loc.strip()
            for loc in _xpath(root, "//*[local-name()='sitemap']/*[local-name()='loc']/text()")
        ]
        return [], [c for c in children if _NEWS_SITEMAP.search(c)]
    return None  # HTML (ví dụ sitemap chuyển hướng về trang chủ) hoặc XML lạ


def _rss(root: etree._Element) -> list[Link]:
    links: list[Link] = []
    for item in _xpath(root, "//*[local-name()='item' or local-name()='entry']"):
        url = _first(item, "link/text()") or _first(item, "*[local-name()='link']/@href")
        if url:
            links.append(
                Link(
                    url.strip(),
                    parse_date(
                        _first(item, "pubDate/text()")
                        or _first(
                            item, "*[local-name()='published' or local-name()='updated']/text()"
                        )
                    ),
                )
            )
    return links


def _urlset(root: etree._Element) -> list[Link]:
    links: list[Link] = []
    for node in _xpath(root, "//*[local-name()='url']"):
        url = _first(node, "*[local-name()='loc']/text()")
        if url:
            # news:publication_date là ngày đăng; lastmod là lần sửa cuối, chỉ dùng dự phòng.
            date = _first(node, ".//*[local-name()='publication_date']/text()") or _first(
                node, "*[local-name()='lastmod']/text()"
            )
            links.append(Link(url.strip(), parse_date(date)))
    return links


def _xpath(node: etree._Element, xpath: str) -> list[Any]:
    result = node.xpath(xpath)
    return result if isinstance(result, list) else []


def _first(node: etree._Element, xpath: str) -> str | None:
    found = _xpath(node, xpath)
    return (str(found[0]).strip() or None) if found else None
