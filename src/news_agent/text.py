"""Chuẩn hóa văn bản, ngày giờ và URL."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from lxml import etree
from lxml import html as lxml_html

VIETNAM = ZoneInfo("Asia/Ho_Chi_Minh")

# ---------------------------------------------------------------- văn bản

_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SPACES = re.compile(r"[ \t  -   　]+")
_TONES = {"a": "àáảãạ", "e": "èéẻẽẹ", "y": "ỳýỷỹỵ", "o": "òóỏõọ", "u": "ùúủũụ"}
_VOWELS = "".join(sorted({c for t in _TONES.values() for c in t} | set("ăâêôơưđ")))
_LETTER = rf"[a-zA-Z{_VOWELS}{_VOWELS.upper()}]"
_SECOND = _TONES["a"] + _TONES["e"] + _TONES["y"]
# Kiểu dấu mới ở cuối âm tiết (oà, oè, uý), không sau "q": đưa về kiểu cũ (òa, òe, úy).
_NEW_STYLE = re.compile(rf"(?<![qQ])([oOuU])([{_SECOND}{_SECOND.upper()}])(?!{_LETTER})")
_OLD_STYLE_PAIRS = {("o", "a"), ("o", "e"), ("u", "y")}


def _to_old_style(match: re.Match[str]) -> str:
    first, toned = match.group(1), match.group(2)
    vowel = next(v for v, tones in _TONES.items() if toned.lower() in tones)
    if (first.lower(), vowel) not in _OLD_STYLE_PAIRS:
        return match.group(0)
    new_first = _TONES[first.lower()][_TONES[vowel].index(toned.lower())]
    return (new_first.upper() if first.isupper() else new_first) + (
        vowel.upper() if toned.isupper() else vowel
    )


def normalize_text(text: str) -> str:
    """NFC, một kiểu đặt dấu, bỏ ký tự vô hình/điều khiển; đoạn cách nhau bằng dòng trống."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    text = _NEW_STYLE.sub(_to_old_style, text)
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return "\n\n".join(line for line in lines if line)


def normalize_line(text: str | None) -> str:
    return " ".join(normalize_text(text or "").split())


_BLOCK_TAGS = frozenset(
    [
        "p",
        "div",
        "section",
        "article",
        "blockquote",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "figcaption",
        "td",
        "th",
        "pre",
        "table",
        "ul",
        "ol",
        "figure",
        "header",
        "footer",
    ]
)
_DROP_XPATH = "//script | //style | //noscript | //iframe | //template | //svg | //form | //button"


def html_to_text(fragment: str | None) -> str:
    """HTML thân bài → đoạn văn. Duyệt cả `tail` để không mất chữ nằm sau thẻ đóng."""
    if not fragment or not fragment.strip():
        return ""
    try:
        root = lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return normalize_text(fragment)
    for junk in root.xpath(_DROP_XPATH):
        junk.drop_tree()
    pieces: list[str] = []
    _walk(root, pieces)
    return normalize_text("".join(pieces))


def _walk(element: lxml_html.HtmlElement, out: list[str]) -> None:
    tag = element.tag if isinstance(element.tag, str) else ""
    if tag == "br" or tag in _BLOCK_TAGS:
        out.append("\n")
    if tag and element.text:
        out.append(element.text)
    for child in element:
        _walk(child, out)
    if tag in _BLOCK_TAGS:
        out.append("\n")
    if element.tail:
        out.append(element.tail)


# ---------------------------------------------------------------- ngày giờ

_GMT = re.compile(r"\(?\s*(?:GMT|UTC)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?\s*\)?", re.I)
# Ngày/tháng/năm kiểu Việt: "24/10/2026, 14:46", "24.10.2026 14h46"
_VN_DATE = re.compile(
    r"(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})[/.-](?P<y>\d{4})"
    r"(?:\s*[,\-–]?\s*(?P<H>\d{1,2})[:h](?P<M>\d{2})(?::(?P<S>\d{2}))?)?"
)
# Tháng/ngày kiểu Mỹ, CHỈ khi có AM/PM: "10/24/2026 2:13:00 PM" (RSS Tuổi Trẻ)
_US_DATE = re.compile(
    r"(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})\s+(?P<H>\d{1,2}):(?P<M>\d{2})"
    r"(?::(?P<S>\d{2}))?\s*(?P<p>AM|PM)",
    re.I,
)


def parse_date(raw: str | None) -> datetime | None:
    """ISO 8601, RFC 2822, kiểu Mỹ có AM/PM, kiểu Việt → datetime UTC. Không múi giờ = giờ VN."""
    if not raw or not raw.strip():
        return None
    value = " ".join(raw.split())
    for parse in (_iso, _rfc2822, _us, _vietnamese):
        if (result := parse(value)) is not None:
            return result.astimezone(UTC)
    return None


def _localize(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=VIETNAM)


def _iso(value: str) -> datetime | None:
    try:
        return _localize(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _rfc2822(value: str) -> datetime | None:
    try:
        return _localize(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError):
        return None


def _us(value: str) -> datetime | None:
    if not (match := _US_DATE.search(value)):
        return None
    hour = int(match["H"]) % 12 + (12 if match["p"].upper() == "PM" else 0)
    return _build(match, hour, value)


def _vietnamese(value: str) -> datetime | None:
    if not (match := _VN_DATE.search(value)):
        return None
    return _build(match, int(match["H"] or 0), value)


def _build(match: re.Match[str], hour: int, value: str) -> datetime | None:
    try:
        naive = datetime(
            int(match["y"]), int(match["m"]), int(match["d"]),
            hour, int(match["M"] or 0), int(match["S"] or 0),
        )  # fmt: skip
    except ValueError:  # 31/02, 25:00...
        return None
    tz = _explicit_tz(value)
    return naive.replace(tzinfo=tz) if tz else _localize(naive)


def _explicit_tz(value: str) -> tzinfo | None:
    if not (match := _GMT.search(value)):
        return None
    sign = 1 if match.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0)))


# ---------------------------------------------------------------- URL

_TRACKING = frozenset({"fbclid", "gclid", "dclid", "msclkid", "_ga", "zarsrc", "gidzl", "ref"})
_AMP_QUERY = {("amp", ""), ("amp", "1"), ("amp", "true"), ("outputtype", "amp")}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize_url(url: str, base: str | None = None) -> str | None:
    """Một bài một URL: bỏ fragment, tham số theo dõi, biến thể AMP, "/" cuối. Sai → None."""
    parts = urlsplit(urljoin(base, url.strip()) if base else url.strip())
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    host = host.removeprefix("amp.")
    netloc = host if parts.port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{parts.port}"
    path = parts.path or "/"
    if path.startswith("/amp/"):
        path = path.removeprefix("/amp")
    if path.rstrip("/").endswith("/amp"):
        path = path.rstrip("/").removesuffix("/amp") or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower() in _TRACKING or k.lower().startswith("utm_"))
        and (k.lower(), v.lower()) not in _AMP_QUERY
    )
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def same_site(url: str, base_url: str) -> bool:
    """vnexpress.net, www.vnexpress.net, amp.vnexpress.net là cùng một site."""

    def root(u: str) -> str:
        return (urlsplit(u).hostname or "").lower().removeprefix("www.").removeprefix("amp.")

    return root(url) == root(base_url)
