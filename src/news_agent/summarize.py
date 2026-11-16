"""Tóm tắt trích xuất (extractive): chọn nguyên câu từ sapo + nội dung, dài 50–70 từ.

1. Tách câu; bỏ tiêu đề phụ, chú thích ảnh/video, câu quá ngắn.
2. Vector TF-IDF cho từng câu: âm tiết + cặp âm tiết liền nhau (xấp xỉ từ ghép tiếng Việt).
3. Điểm = 0.5·TextRank + 0.3·vị trí (tin tức đặt ý chính ở đầu) + 0.2·độ giống tiêu đề.
4. Chọn câu điểm cao nhất, bỏ câu trùng ý (cosine > 0.5 với câu đã chọn), dừng khi ≥ min_words;
   câu không vừa thì cắt ở dấu phẩy/chấm phẩy (hoặc ở từ thứ max_words, thêm "…").
5. Ghép theo thứ tự xuất hiện trong bài.
"Từ" = chuỗi cách nhau bởi khoảng trắng (một tiếng): "Hà Nội" là 2 từ.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

MIN_WORDS = 50
MAX_WORDS = 70
MAX_CANDIDATES = 60  # bài rất dài: ý chính nằm ở phần đầu
REDUNDANT = 0.5  # cosine; 0.6 để lọt câu chú thích lặp lại sapo (đo trên bài thật)

_ABBREV = frozenset(
    {
        "tp",
        "ts",
        "ths",
        "pgs",
        "gs",
        "bs",
        "ks",
        "th",
        "pv",
        "nxb",
        "mr",
        "mrs",
        "ms",
        "dr",
        "st",
        "no",
    }
)
# Ngoặc đóng sau dấu chấm ("Tôi đi.") ở lại với câu trước.
_END = re.compile(r"(?:(?<=[.!?…])|(?<=[.!?…][\"”’)\]]))\s+(?=[\"“(\[]?[A-ZĐÀ-Ỹ0-9])")
_CAPTION = re.compile(
    r"(?:^|\s|[-–(])(?:Ảnh|Video|Clip|Đồ họa|Đồ hoạ|Nguồn|Infographic)(?:\s+minh họa)?\s*:", re.I
)
_TERMINAL = re.compile(r"[.!?…:;\"”)]$")
_TOKEN = re.compile(r"\w+")
_CLAUSE = re.compile(r"[,;:–—]\s")
_STOPWORD_TEXT = (
    "và của là có được cho các những một trong với này đã không để thì mà khi về từ theo "
    "tại đến như cũng nhưng vì nên do bị sẽ đang rất hơn nhiều sau trước ra vào lên lại "
    "đó kia ấy họ anh chị ông bà tôi chúng ta mình nó người năm ngày tháng"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def summarize(
    title: str,
    sapo: str,
    content: str,
    *,
    min_words: int = MIN_WORDS,
    max_words: int = MAX_WORDS,
) -> str:
    sentences = _candidates(sapo, content)
    if not sentences:
        return _cut(" ".join(f"{sapo} {content}".split()), max_words)
    if sum(_words(s) for s in sentences) <= max_words:
        return " ".join(sentences)  # bài ngắn: lấy hết (có thể < min_words)

    vectors = _tfidf([_terms(s) for s in sentences])
    title_vec = _tfidf_query(_terms(title), [_terms(s) for s in sentences])
    rank = _normalize(_textrank(vectors))
    n = len(sentences)
    scores = [
        0.5 * rank[i] + 0.3 * (1 - i / n) + 0.2 * _cosine(vectors[i], title_vec) for i in range(n)
    ]

    chosen: dict[int, str] = {}
    total = 0
    order = sorted(range(n), key=lambda i: -scores[i])
    for i in order:
        if total >= min_words:
            break
        if any(_cosine(vectors[i], vectors[j]) > REDUNDANT for j in chosen):
            continue
        length = _words(sentences[i])
        if total + length <= max_words:
            chosen[i], total = sentences[i], total + length
    if total < min_words:  # không câu nào vừa: cắt câu tốt nhất còn lại cho đủ độ dài
        for i in order:
            if i in chosen or any(_cosine(vectors[i], vectors[j]) > REDUNDANT for j in chosen):
                continue
            chosen[i] = _trim(sentences[i], min_words - total, max_words - total)
            break
    return " ".join(chosen[i] for i in sorted(chosen))


# ---------------------------------------------------------------- câu


def split_sentences(paragraph: str) -> list[str]:
    parts: list[str] = []
    for piece in _END.split(paragraph.strip()):
        # "TP. HCM", "PGS. TS. Nguyễn": dấu chấm sau viết tắt không kết thúc câu.
        last = parts[-1].rstrip(".").rsplit(maxsplit=1)[-1].casefold() if parts else ""
        if parts and (last in _ABBREV or (len(last) == 1 and last.isalpha())):
            parts[-1] = f"{parts[-1]} {piece}"
        elif piece:
            parts.append(piece)
    return parts


def _candidates(sapo: str, content: str) -> list[str]:
    result: list[str] = []
    for paragraph in [sapo, *content.split("\n\n")]:
        paragraph = " ".join(paragraph.split())
        # Tiêu đề phụ / chú thích: ngắn và không kết thúc bằng dấu câu.
        if not paragraph or (_words(paragraph) < 20 and not _TERMINAL.search(paragraph)):
            continue
        for sentence in split_sentences(paragraph):
            if _words(sentence) >= 6 and not _CAPTION.search(sentence):
                result.append(sentence)
        if len(result) >= MAX_CANDIDATES:
            break
    return result[:MAX_CANDIDATES]


def _words(text: str) -> int:
    return len(text.split())


def _trim(sentence: str, low: int, high: int) -> str:
    """Cắt ở ranh giới mệnh đề gần high nhất (≥ low từ); không có thì cắt ở từ thứ high."""
    best = None
    for match in _CLAUSE.finditer(sentence):
        head = sentence[: match.start()]
        if low <= _words(head) <= high:
            best = head
    return (best.rstrip(" ,;:–—") + "…") if best else _cut(sentence, high)


def _cut(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]).rstrip(",;:") + "…"


# ---------------------------------------------------------------- TF-IDF, TextRank


def _terms(text: str) -> list[str]:
    syllables = [t for t in _TOKEN.findall(text.casefold()) if not t.isdigit()]
    unigrams = [s for s in syllables if s not in _STOPWORDS]
    bigrams = [f"{a}_{b}" for a, b in zip(syllables, syllables[1:], strict=False)]
    return unigrams + bigrams


def _idf(docs: Sequence[list[str]]) -> dict[str, float]:
    df = Counter(term for doc in docs for term in set(doc))
    return {term: math.log((1 + len(docs)) / (1 + count)) + 1 for term, count in df.items()}


def _tfidf(docs: Sequence[list[str]]) -> list[dict[str, float]]:
    idf = _idf(docs)
    return [{t: c * idf[t] for t, c in Counter(doc).items()} for doc in docs]


def _tfidf_query(query: list[str], docs: Sequence[list[str]]) -> dict[str, float]:
    idf = _idf(docs)
    return {t: c * idf[t] for t, c in Counter(query).items() if t in idf}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def _textrank(
    vectors: list[dict[str, float]], damping: float = 0.85, iters: int = 30
) -> list[float]:
    """PageRank trên đồ thị câu, cạnh = độ giống cosine."""
    n = len(vectors)
    weights = [
        [_cosine(vectors[i], vectors[j]) if i != j else 0.0 for j in range(n)] for i in range(n)
    ]
    out = [sum(row) or 1.0 for row in weights]
    rank = [1.0 / n] * n
    for _ in range(iters):
        rank = [
            (1 - damping) / n + damping * sum(weights[j][i] / out[j] * rank[j] for j in range(n))
            for i in range(n)
        ]
    return rank


def _normalize(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    return [(v - low) / (high - low) if high > low else 1.0 for v in values]
