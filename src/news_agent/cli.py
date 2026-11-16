"""CLI: `news-agent run` crawl mọi bài trong ngày của mọi nguồn và ghi JSON."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from news_agent import __version__
from news_agent.config import (
    DEFAULT_CONFIG_DIR,
    Config,
    ConfigError,
    Selectors,
    SourceConfig,
    load_config,
    sources_json_schema,
)
from news_agent.crawl import crawl
from news_agent.export import dumps, to_dict, write_run_file
from news_agent.models import TimeWindow
from news_agent.text import VIETNAM

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
out = Console()  # stdout: kết quả
err = Console(stderr=True)  # stderr: tiến độ, lỗi

ConfigDir = Annotated[
    Path, typer.Option("--config-dir", "-c", envvar="NEWS_AGENT_CONFIG_DIR", help="Thư mục config.")
]


def _load(config_dir: Path) -> Config:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        err.print(str(exc), style="red", markup=False, highlight=False, soft_wrap=True)
        raise typer.Exit(1) from None


@app.command()
def run(
    config_dir: ConfigDir = DEFAULT_CONFIG_DIR,
    day: Annotated[
        datetime | None,
        typer.Option(
            "--date", "-d", formats=["%Y-%m-%d"], help="Ngày cần lấy (giờ VN). Mặc định: hôm nay."
        ),
    ] = None,
    source_ids: Annotated[
        list[str] | None, typer.Option("--source", "-s", help="Chỉ các nguồn này (lặp lại được).")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", "-n", min=1, help="Tối đa N bài mỗi nguồn (thử nhanh).")
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir", "-o", file_okay=False, help="Thư mục ghi run-<ngày-giờ>.json."
        ),
    ] = Path("data/runs"),
    stdout_json: Annotated[
        bool, typer.Option("--json", help="In JSON ra stdout thay cho bảng.")
    ] = False,
    log_level: Annotated[
        str, typer.Option("--log-level", "-l", help="Log của Scrapy.")
    ] = "WARNING",
) -> None:
    """Tự tìm và crawl mọi bài trong ngày, phân loại theo topics.json, ghi JSON."""
    config = _load(config_dir)
    sources = _select(config, source_ids or [])
    target = day.date() if day else datetime.now(VIETNAM).date()
    window = TimeWindow.for_day(target, VIETNAM)
    err.print(f"Ngày {target:%d/%m/%Y} (giờ VN) · {', '.join(s.id for s in sources)}")

    progress = Progress(
        TextColumn("{task.description:<10}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=err,
    )  # fmt: skip
    tasks = {s.id: progress.add_task(s.id, total=None) for s in sources}
    with progress:
        result = crawl(
            config,
            sources,
            window,
            limit=limit,
            log_level=log_level.upper(),
            on_progress=lambda sid, done, total: progress.update(
                tasks[sid], completed=done, total=total
            ),
        )

    generated_at = datetime.now(UTC)
    data = to_dict(result, generated_at)
    text = dumps(data)
    path = write_run_file(output_dir, text, generated_at)
    err.print(f"Đã ghi {data['total_articles']} bài vào {path}", markup=False, soft_wrap=True)
    if stdout_json:
        typer.echo(text)
    else:
        out.print(_runs_table(data))
    if result.crashed_sources or any(r["status"] == "failed" for r in data["runs"]):
        raise typer.Exit(1)


@app.command()
def sources(config_dir: ConfigDir = DEFAULT_CONFIG_DIR) -> None:
    """Liệt kê nguồn trong sources.json và kiểm tra cấu hình."""
    config = _load(config_dir)
    table = Table(title=f"{len(config.sources)} nguồn · {len(config.topics.topics)} topic")
    for column in ("ID", "Tên", "URL", "Selector", "Bật", "JS", "Giãn cách"):
        table.add_column(column)
    for s in config.sources:
        custom = s.selectors != Selectors()
        table.add_row(
            s.id, s.name, s.url, "riêng" if custom else "tự động", "✓" if s.enabled else "✗",
            "✓" if s.render_js else "", f"{s.rate_limit_seconds:g}s",
        )  # fmt: skip
    out.print(table)


@app.command()
def schema() -> None:
    """In JSON Schema của sources.json: news-agent schema > config/sources.schema.json"""
    typer.echo(json.dumps(sources_json_schema(), ensure_ascii=False, indent=2))


def _select(config: Config, ids: list[str]) -> list[SourceConfig]:
    known = {s.id: s for s in config.sources}
    if unknown := [i for i in ids if i not in known]:
        raise typer.BadParameter(f"không có nguồn {', '.join(unknown)}; có: {', '.join(known)}")
    selected = [known[i] for i in dict.fromkeys(ids)] or config.enabled_sources()
    if not selected:
        raise typer.BadParameter("không có nguồn nào đang bật")
    return selected


def _runs_table(data: dict[str, Any]) -> Table:
    table = Table(title=f"{data['total_articles']} bài")
    for column in (
        "Nguồn",
        "Trạng thái",
        "Link",
        "Ngoài ngày",
        "Bài",
        "Lỗi",
        "Chưa phân loại",
        "Giây",
    ):
        table.add_column(column, justify="right")
    for r in data["runs"]:
        s = r["stats"]
        table.add_row(
            r["source_id"], r["status"], str(s["discovered"]), str(s.get("out_of_window", 0)),
            str(s["new"]), str(s["parse_failed"] + s["errors"]), str(s.get("unclassified", 0)),
            f"{r['duration_seconds']:.0f}",
        )  # fmt: skip
    return table


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"news-agent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_show_version, is_eager=True, help="In phiên bản.")
    ] = False,
) -> None:
    """Crawl tin tức từ các báo trong config/sources.json, phân loại theo config/topics.json."""
