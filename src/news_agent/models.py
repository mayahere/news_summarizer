"""Kiểu dữ liệu dùng chung. Tên trường Article là tên trường trong file JSON đầu ra."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """Khoảng nửa mở [start, end): 00:00 ngày 28 thuộc ngày 28, không thuộc ngày 27."""

    start: datetime
    end: datetime

    @classmethod
    def for_day(cls, day: date, tz: tzinfo) -> TimeWindow:
        start = datetime.combine(day, time.min, tzinfo=tz)
        return cls(start, start + timedelta(days=1))

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True, slots=True)
class Link:
    """Link bài tìm được từ RSS/sitemap/trang chủ, trước khi tải."""

    url: str
    published_at: datetime | None = None


@dataclass(slots=True)
class Article:
    source_id: str
    url: str
    canonical_url: str
    title: str
    content: str
    published_at: datetime
    crawled_at: datetime
    sapo: str = ""
    summary: str = ""  # tóm tắt trích xuất 50–70 từ (summarize.py)
    author: str | None = None
    source_category: str | None = None
    image_url: str | None = None
    topic: str | None = None
    topic_confidence: float | None = None
    content_hash: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(slots=True)
class SourceRun:
    """Thống kê một nguồn trong một lần chạy."""

    source_id: str
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None
    stats: Counter[str] = field(default_factory=Counter)
    failures: list[dict[str, str]] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(uuid4()))

    def fail(self, key: str, url: str, reason: str) -> None:
        self.stats[key] += 1
        self.failures.append({"url": url, "reason": reason})

    @property
    def status(self) -> str:
        """failed: không tìm được link nào; partial: hơn nửa số bài không bóc tách được."""
        if self.stats["discovered"] == 0:
            return "failed"
        processed = self.stats["new"] + self.stats["parse_failed"]
        return (
            "partial" if processed and self.stats["parse_failed"] / processed > 0.5 else "success"
        )
