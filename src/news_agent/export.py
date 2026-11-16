"""Kết quả chạy → JSON (format_version 3): mọi bài gộp trong `articles`, mới nhất trước."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from news_agent.crawl import CrawlResult
from news_agent.models import SourceRun
from news_agent.text import VIETNAM

FORMAT_VERSION = 3  # 3: thêm articles[].summary
STAT_KEYS = ("discovered", "new", "duplicate_url", "parse_failed", "errors", "skipped_robots")


def to_dict(result: CrawlResult, generated_at: datetime) -> dict[str, Any]:
    articles = sorted(result.articles, key=lambda a: a.published_at, reverse=True)
    return {
        "format_version": FORMAT_VERSION,
        "generated_at": generated_at.isoformat(),
        "window": {"start": result.window.start.isoformat(), "end": result.window.end.isoformat()},
        "total_articles": len(articles),
        "articles": [
            {k: v.isoformat() if isinstance(v, datetime) else v for k, v in asdict(a).items()}
            for a in articles
        ],
        "runs": [_run(r) for r in result.runs],
        "crashed_sources": result.crashed_sources,
    }


def _run(run: SourceRun) -> dict[str, Any]:
    ended = run.ended_at or run.started_at
    return {
        "run_id": run.run_id,
        "source_id": run.source_id,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": round((ended - run.started_at).total_seconds(), 3),
        "error": "không tìm thấy link bài nào" if run.status == "failed" else None,
        "stats": {**dict.fromkeys(STAT_KEYS, 0), **run.stats},
        "article_count": run.stats["new"],
        "failures": run.failures,
    }


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)  # giữ nguyên tiếng Việt


def write_run_file(directory: Path, text: str, at: datetime) -> Path:
    """directory/run-2026-10-27_17-06-18.json (giờ VN); trùng giây thì thêm -1, -2..."""
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f"run-{at.astimezone(VIETNAM):%Y-%m-%d_%H-%M-%S}.json"
    path, counter = base, 1
    while path.exists():
        path, counter = base.with_name(f"{base.stem}-{counter}.json"), counter + 1
    path.write_text(text + "\n", encoding="utf-8")
    return path
