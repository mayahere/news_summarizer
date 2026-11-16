"""Spider Scrapy: với mỗi nguồn, tự tìm link bài → tải → bóc tách → phân loại → yield Article.

Tìm link theo thứ tự, dừng ở bước đầu tiên cho kết quả:
    1. robots.txt → Google News sitemap (đủ bài 2 ngày, có ngày đăng, ít request nhất)
    2. Không có/không đọc được → mọi RSS trên trang chủ và trang /rss
    3. Link bài trên trang chủ (chưa biết ngày) → tải sau cùng, khi spider rảnh
Link có ngày ngoài cửa sổ bị bỏ trước khi tải.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

import scrapy
from scrapy import signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DontCloseSpider, IgnoreRequest
from scrapy.http import Response, TextResponse
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.python.failure import Failure

from news_agent.classify import TopicClassifier
from news_agent.config import SourceConfig
from news_agent.discovery import article_links, feed_links, news_sitemaps, parse_feed
from news_agent.extract import ExtractError, extract_article
from news_agent.models import Article, Link, SourceRun, TimeWindow
from news_agent.summarize import summarize
from news_agent.text import canonicalize_url, same_site

ProgressCallback = Callable[[str, int, int], None]  # (source_id, đã xử lý, đã lên lịch)
DISCOVERY_PRIORITY = 10**12  # tìm link trước, rồi mới tải bài (bài mới nhất trước)


class NewsSpider(scrapy.Spider):
    name = "news"

    def __init__(
        self,
        *,
        sources: Iterable[SourceConfig],
        window: TimeWindow,
        classifier: TopicClassifier,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.sources = {s.id: s for s in sources}
        self.window = window
        self.classifier = classifier
        self.limit = limit
        self.on_progress = on_progress
        now = datetime.now(UTC)
        self.runs = {sid: SourceRun(sid, started_at=now) for sid in self.sources}
        self._seen: defaultdict[str, set[str]] = defaultdict(set)
        self._scheduled: Counter[str] = Counter()
        self._done: Counter[str] = Counter()
        self._sitemaps_pending: Counter[str] = Counter()
        self._fallback_started: set[str] = set()
        self._undated: defaultdict[str, list[Link]] = defaultdict(list)

    @classmethod
    def from_crawler(cls, crawler: Crawler, *args: Any, **kwargs: Any) -> NewsSpider:
        spider: NewsSpider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_idle, signal=signals.spider_idle)
        return spider

    # ------------------------------------------------------------- tìm link

    async def start(self) -> AsyncIterator[scrapy.Request]:
        for source in self.sources.values():
            yield self._discover(f"{source.url}/robots.txt", source, self.parse_robots, "robots")
            for feed in source.feeds:
                yield self._discover(str(feed), source, self.parse_feed, "feed")

    def parse_robots(self, response: Response) -> Iterator[scrapy.Request]:
        source = self._source(response)
        sitemaps = news_sitemaps(response.text) if isinstance(response, TextResponse) else []
        if not sitemaps:
            yield from self._fallback(source)
        for url in sitemaps:
            self._sitemaps_pending[source.id] += 1
            yield self._discover(url, source, self.parse_news_sitemap, "sitemap")

    def parse_news_sitemap(self, response: Response) -> Iterator[scrapy.Request]:
        source = self._source(response)
        # Sitemap chuyển hướng về trang chủ HTML (VnExpress chỉ phục vụ công cụ tìm kiếm) → None
        links, children = parse_feed(response.body) or ([], [])
        for url in children:
            self._sitemaps_pending[source.id] += 1
            yield self._discover(url, source, self.parse_news_sitemap, "sitemap")
        yield from self._schedule(source, links)
        yield from self._sitemap_done(source)

    def parse_page(self, response: Response) -> Iterator[scrapy.Request]:
        source = self._source(response)
        content_type = (response.headers.get("Content-Type") or b"").decode().lower()
        if isinstance(response, TextResponse) and "html" in content_type:
            for url in feed_links(response.text, response.url):
                yield self._discover(url, source, self.parse_feed, "feed")
            self._undated[source.id] += article_links(response.text, response.url)

    def parse_feed(self, response: Response) -> Iterator[scrapy.Request]:
        links, _ = parse_feed(response.body) or ([], [])
        yield from self._schedule(self._source(response), links)

    def on_discovery_error(self, failure: Failure) -> Iterator[scrapy.Request]:
        request = failure.request  # type: ignore[attr-defined]
        source = self.sources[request.meta["source_id"]]
        self.logger.debug("discovery_failed %s: %s", request.url, failure.value)
        if request.meta["role"] == "robots":
            yield from self._fallback(source)
        elif request.meta["role"] == "sitemap":
            yield from self._sitemap_done(source)

    def _sitemap_done(self, source: SourceConfig) -> Iterator[scrapy.Request]:
        self._sitemaps_pending[source.id] -= 1
        if self._sitemaps_pending[source.id] == 0 and not self.runs[source.id].stats["discovered"]:
            yield from self._fallback(source)

    def _fallback(self, source: SourceConfig) -> Iterator[scrapy.Request]:
        if source.id not in self._fallback_started:
            self._fallback_started.add(source.id)
            yield self._discover(source.url, source, self.parse_page, "page")
            yield self._discover(f"{source.url}/rss", source, self.parse_page, "page")

    def _on_idle(self) -> None:
        """Hết việc: tải nốt link trang chủ chưa có trong RSS/sitemap (chưa biết ngày đăng)."""
        scheduled = False
        for source_id, links in list(self._undated.items()):
            self._undated[source_id] = []
            for request in self._schedule(self.sources[source_id], links):
                self.crawler.engine.crawl(request)
                scheduled = True
        if scheduled:
            raise DontCloseSpider

    def _discover(self, url: str, source: SourceConfig, callback: Any, role: str) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback,
            errback=self.on_discovery_error,
            meta={"source_id": source.id, "role": role},
            priority=DISCOVERY_PRIORITY,
        )

    def _schedule(self, source: SourceConfig, links: Iterable[Link]) -> Iterator[scrapy.Request]:
        run = self.runs[source.id]
        for link in links:
            url = canonicalize_url(link.url)
            if not url or not same_site(url, source.url):
                continue
            if url in self._seen[source.id]:
                run.stats["duplicate_url"] += 1  # cùng bài xuất hiện ở nhiều RSS/sitemap
                continue
            self._seen[source.id].add(url)
            run.stats["discovered"] += 1
            if link.published_at and not self.window.contains(link.published_at):
                run.stats["out_of_window"] += 1
                continue
            if self.limit is not None and self._scheduled[source.id] >= self.limit:
                continue
            self._scheduled[source.id] += 1
            self._report(source.id)
            yield scrapy.Request(
                url,
                self.parse_article,
                errback=self.on_article_error,
                meta={
                    "source_id": source.id,
                    "published_at": link.published_at,
                    "playwright": source.render_js,
                },
                # Mới nhất tải trước; link chưa biết ngày (từ trang chủ) tải sau cùng.
                priority=int(link.published_at.timestamp()) if link.published_at else 0,
            )

    # ------------------------------------------------------------- bài viết

    def parse_article(self, response: Response) -> Iterator[Article]:
        source = self._source(response)
        run = self.runs[source.id]
        self._finish_one(source.id)
        if not isinstance(response, TextResponse):
            run.fail("parse_failed", response.url, "không phải HTML")
            return
        try:
            article = extract_article(
                response.text,
                response.url,
                source_id=source.id,
                rules=source.selectors,
                crawled_at=datetime.now(UTC),
                fallback_published_at=response.meta.get("published_at"),
            )
        except ExtractError as exc:
            run.fail("parse_failed", response.url, str(exc))
            return
        # Link từ trang chủ chưa biết ngày: chỉ biết sau khi đọc trang.
        if not self.window.contains(article.published_at):
            run.stats["out_of_window"] += 1
            return
        self.classifier.classify(article)
        article.summary = summarize(article.title, article.sapo, article.content)
        if article.topic is None:
            run.stats["unclassified"] += 1
        run.stats["new"] += 1
        yield article

    def on_article_error(self, failure: Failure) -> None:
        request = failure.request  # type: ignore[attr-defined]
        source_id = request.meta["source_id"]
        self._finish_one(source_id)
        run, error = self.runs[source_id], failure.value
        if isinstance(error, HttpError):
            run.fail("errors", request.url, f"HTTP {error.response.status}")
        elif isinstance(error, IgnoreRequest):  # RobotsTxtMiddleware
            run.stats["skipped_robots"] += 1
        else:
            run.fail("errors", request.url, failure.getErrorMessage()[:200])

    # ------------------------------------------------------------- tiện ích

    def _source(self, response: Response) -> SourceConfig:
        return self.sources[response.meta["source_id"]]

    def _finish_one(self, source_id: str) -> None:
        self._done[source_id] += 1
        self.runs[source_id].ended_at = datetime.now(UTC)
        self._report(source_id)

    def _report(self, source_id: str) -> None:
        if self.on_progress:
            self.on_progress(source_id, self._done[source_id], self._scheduled[source_id])
