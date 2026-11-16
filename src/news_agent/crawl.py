"""Chạy NewsSpider bằng Scrapy và gom kết quả."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from scrapy import signals
from scrapy.crawler import CrawlerProcess

from news_agent.classify import TopicClassifier
from news_agent.config import Config, SourceConfig
from news_agent.models import Article, SourceRun, TimeWindow
from news_agent.spider import NewsSpider, ProgressCallback

USER_AGENT = "NewsAggregatorAgent/1.0 (+mailto:contact@example.com)"


@dataclass(slots=True)
class CrawlResult:
    window: TimeWindow
    articles: list[Article]
    runs: list[SourceRun]
    crashed_sources: list[str] = field(default_factory=list)


def scrapy_settings(sources: list[SourceConfig], log_level: str) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "USER_AGENT": USER_AGENT,
        "ROBOTSTXT_OBEY": True,
        "COOKIES_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_LEVEL": log_level,
        "CONCURRENT_REQUESTS": 32,  # nhiều báo chạy song song...
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,  # ...nhưng mỗi báo một request một lúc
        "DOWNLOAD_DELAY": max((s.rate_limit_seconds for s in sources), default=1.5),
        "DOWNLOAD_DELAY_JITTER": 0,  # giãn cách đúng như cấu hình, không ngẫu nhiên ngắn hơn
        # Giãn cách riêng từng báo theo rate_limit_seconds.
        "DOWNLOAD_SLOTS": {
            urlsplit(s.url).hostname: {"concurrency": 1, "delay": s.rate_limit_seconds}
            for s in sources
        },
        "DOWNLOAD_TIMEOUT": 20,
        "DOWNLOAD_MAXSIZE": 5_000_000,
        "RETRY_TIMES": 3,
    }
    if any(s.render_js for s in sources):
        # Chỉ request có meta "playwright": True mới đi qua trình duyệt; còn lại vẫn là HTTP thường.
        handler = "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
        settings["DOWNLOAD_HANDLERS"] = {"http": handler, "https": handler}
        settings["PLAYWRIGHT_BROWSER_TYPE"] = "chromium"
    return settings


def crawl(
    config: Config,
    sources: list[SourceConfig],
    window: TimeWindow,
    *,
    limit: int | None = None,
    log_level: str = "WARNING",
    on_progress: ProgressCallback | None = None,
) -> CrawlResult:
    """Chạy một lần, chặn tới khi xong. Reactor của Twisted chỉ chạy được một lần mỗi tiến trình."""
    process = CrawlerProcess(scrapy_settings(sources, log_level))
    crawler = process.create_crawler(NewsSpider)
    articles: list[Article] = []
    crashed: set[str] = set()

    def on_item(item: Article, **_: Any) -> None:
        articles.append(item)

    def on_error(failure: Any, response: Any, **_: Any) -> None:
        crashed.add(response.meta.get("source_id", "?"))  # lỗi lập trình trong callback

    crawler.signals.connect(on_item, signal=signals.item_scraped)
    crawler.signals.connect(on_error, signal=signals.spider_error)
    process.crawl(
        crawler,
        sources=sources,
        window=window,
        classifier=TopicClassifier.from_config(config),
        limit=limit,
        on_progress=on_progress,
    )
    process.start()

    spider: NewsSpider = crawler.spider  # type: ignore[assignment]
    now = datetime.now(UTC)
    for run in spider.runs.values():
        run.ended_at = run.ended_at or now
    return CrawlResult(window, articles, list(spider.runs.values()), sorted(crashed))
