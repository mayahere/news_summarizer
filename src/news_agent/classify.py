"""Phân loại bài vào các topic trong config/topics.json.

Thử lần lượt, dừng ở luật đầu tiên khớp:
    1. category_map riêng của nguồn (chuyên mục gốc → topic)            confidence 1.0
    2. Chuyên mục gốc trùng một tên trong topics.json                    0.9
    3. Chuyên mục gốc CHỨA một tên trong topics.json ("Tin tức công nghệ") 0.8
    4. Từ khóa: tiêu đề ×2, sapo ×1; confidence = điểm thắng / (tổng điểm + 1), tối đa 0.8
Dưới min_confidence thì không gán topic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from news_agent.config import Config
from news_agent.models import Article
from news_agent.text import normalize_line


def _key(text: str | None) -> str:
    # Không phân biệt hoa/thường, NFC, kiểu dấu: "Văn hoá" == "văn hóa"
    return normalize_line(text).casefold()


def _phrase(text: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(_key(text))}(?!\w)")  # trọn cụm: "chip" ≠ "chipset"


class TopicClassifier:
    def __init__(
        self,
        *,
        categories: Mapping[str, Iterable[str]],
        keywords: Mapping[str, Iterable[str]],
        source_maps: Mapping[str, Mapping[str, str]] | None = None,
        min_confidence: float = 0.45,
    ) -> None:
        self._source_maps = {
            sid: {_key(name): topic for name, topic in mapping.items()}
            for sid, mapping in (source_maps or {}).items()
        }
        self._categories = {_key(n): t for t, names in categories.items() for n in names}
        # Tên dài thử trước: "khoa học công nghệ" phải thắng "khoa học".
        self._contained = [
            (_phrase(n), t) for n, t in sorted(self._categories.items(), key=lambda kv: -len(kv[0]))
        ]
        self._keywords = {t: [_phrase(k) for k in words] for t, words in keywords.items()}
        self._min_confidence = min_confidence

    @classmethod
    def from_config(cls, config: Config) -> TopicClassifier:
        topics = config.topics
        return cls(
            categories={t: r.categories for t, r in topics.topics.items()},
            keywords={t: r.keywords for t, r in topics.topics.items()},
            source_maps={s.id: s.category_map for s in config.sources},
            min_confidence=topics.min_confidence,
        )

    def classify(self, article: Article) -> None:
        """Gán article.topic/topic_confidence nếu đủ tin cậy."""
        topic, confidence = self.predict(article)
        if topic and confidence >= self._min_confidence:
            article.topic, article.topic_confidence = topic, confidence

    def predict(self, article: Article) -> tuple[str | None, float]:
        return self._by_category(article) or self._by_keywords(article) or (None, 0.0)

    def _by_category(self, article: Article) -> tuple[str, float] | None:
        category = _key(article.source_category)
        if not category:
            return None
        if topic := self._source_maps.get(article.source_id, {}).get(category):
            return topic, 1.0
        if topic := self._categories.get(category):
            return topic, 0.9
        for pattern, topic in self._contained:
            if pattern.search(category):
                return topic, 0.8
        return None

    def _by_keywords(self, article: Article) -> tuple[str, float] | None:
        title, sapo = _key(article.title), _key(article.sapo)
        scores = {
            topic: score
            for topic, patterns in self._keywords.items()
            if (score := sum(2 * bool(p.search(title)) + bool(p.search(sapo)) for p in patterns))
        }
        if not scores:
            return None
        best = max(scores, key=lambda t: scores[t])
        return best, round(min(scores[best] / (sum(scores.values()) + 1), 0.8), 3)
