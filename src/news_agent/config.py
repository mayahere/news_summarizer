"""Đọc và kiểm tra config/sources.json và config/topics.json. Mọi lỗi được gom rồi báo một lần."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator

DEFAULT_CONFIG_DIR = Path("config")
SOURCES_FILE = "sources.json"
TOPICS_FILE = "topics.json"


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Cấu hình không hợp lệ:\n" + "\n".join(f"  - {p}" for p in problems))
        self.problems = problems


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # gõ sai tên khóa là lỗi


class Selectors(_Strict):
    """CSS selector riêng của một báo. Tất cả tùy chọn: trường nào bỏ trống (hoặc selector không
    khớp) thì lấy từ JSON-LD → thẻ meta OpenGraph → trafilatura."""

    title: str | None = None
    sapo: str | None = None
    content: str | None = None  # khối thân bài
    author: str | None = None
    published: str | None = None
    category: str | None = None  # phần tử đầu tiên = chuyên mục cấp cao nhất
    remove: tuple[str, ...] = ()  # rác nằm trong thân bài: tin liên quan, quảng cáo...


class SourceConfig(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    name: str = Field(min_length=1)
    base_url: HttpUrl
    enabled: bool = True
    rate_limit_seconds: float = Field(default=1.5, ge=1.0)  # giãn cách request cùng domain
    render_js: bool = False  # True: tải trang bài bằng trình duyệt (Playwright)
    feeds: list[HttpUrl] = Field(default_factory=list)  # thêm RSS/sitemap ngoài phần tự tìm
    category_map: dict[str, str] = Field(default_factory=dict)  # chuyên mục gốc → topic
    selectors: Selectors = Field(default_factory=Selectors)  # không khai báo: tự bóc tách

    @property
    def url(self) -> str:
        return str(self.base_url).rstrip("/")


class TopicRule(_Strict):
    categories: list[str] = Field(default_factory=list)  # tên chuyên mục của các báo
    keywords: list[str] = Field(default_factory=list)  # cụm từ trong tiêu đề/sapo


class TopicsFile(_Strict):
    min_confidence: float = Field(default=0.45, ge=0, le=1)  # dưới ngưỡng → topic = null
    topics: dict[str, TopicRule] = Field(min_length=1)

    @model_validator(mode="after")
    def _category_in_one_topic(self) -> TopicsFile:
        owner: dict[str, str] = {}
        for topic, rule in self.topics.items():
            for name in rule.categories:
                key = name.strip().casefold()
                if owner.setdefault(key, topic) != topic:
                    raise ValueError(f"chuyên mục {name!r} thuộc cả {owner[key]} và {topic}")
        return self


@dataclass(frozen=True, slots=True)
class Config:
    sources: tuple[SourceConfig, ...]
    topics: TopicsFile

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def load_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> Config:
    problems: list[str] = []
    sources = _load_sources(config_dir / SOURCES_FILE, problems)
    topics = _load_topics(config_dir / TOPICS_FILE, problems)
    if topics is not None:
        for source in sources:
            for category, topic in source.category_map.items():
                if topic not in topics.topics:
                    problems.append(
                        f"{SOURCES_FILE}: {source.id}.category_map[{category!r}]: "
                        f"topic {topic!r} không có trong {TOPICS_FILE}"
                    )
    if problems or topics is None:
        raise ConfigError(problems)
    return Config(tuple(sources), topics)


def _load_sources(path: Path, problems: list[str]) -> list[SourceConfig]:
    """{"$schema": ..., "defaults": {...}, "sources": [{...}]}; defaults gộp dưới từng nguồn."""
    data = _read_json(path, problems)
    if data is None:
        return []
    unknown = set(data) - {"$schema", "defaults", "sources"}
    problems += [f"{path}: {key}: khóa không hợp lệ" for key in sorted(unknown)]
    defaults = data.get("defaults") or {}
    entries = data.get("sources")
    if not isinstance(defaults, dict) or not isinstance(entries, list) or not entries:
        problems.append(f"{path}: cần 'defaults' là object và 'sources' là mảng không rỗng")
        return []

    sources: list[SourceConfig] = []
    for index, entry in enumerate(entries):
        label = f"sources[{index}]" + (f"({entry.get('id')})" if isinstance(entry, dict) else "")
        try:
            source = SourceConfig(**{**defaults, **entry})
        except (ValidationError, TypeError) as exc:
            problems += _format(path, label, exc)
            continue
        if any(s.id == source.id for s in sources):
            problems.append(f"{path}: {label}: id {source.id!r} bị trùng")
            continue
        sources.append(source)
    return sources


def _load_topics(path: Path, problems: list[str]) -> TopicsFile | None:
    data = _read_json(path, problems)
    if data is None:
        return None
    data.pop("$schema", None)
    try:
        return TopicsFile(**data)
    except ValidationError as exc:
        problems += _format(path, "", exc)
        return None


def _read_json(path: Path, problems: list[str]) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"{path}: không tồn tại")
        return None
    except json.JSONDecodeError as exc:
        problems.append(f"{path}: lỗi cú pháp JSON: {exc}")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path}: gốc phải là object")
        return None
    return data


def _format(path: Path, label: str, exc: ValidationError | TypeError) -> list[str]:
    if isinstance(exc, TypeError):
        return [f"{path}: {label}: phải là object"]
    return [
        f"{path}: {'.'.join(p for p in (label, *map(str, e['loc'])) if p)}: {e['msg']}"
        for e in exc.errors()
    ]


def sources_json_schema() -> dict[str, Any]:
    """JSON Schema cho sources.json để editor gợi ý khóa.

    Sinh lại: news-agent schema > config/sources.schema.json
    """
    source = SourceConfig.model_json_schema()
    defs = source.pop("$defs", {})
    defaults = {k: v for k, v in source.items() if k not in ("required", "title")}
    defaults["properties"] = {k: v for k, v in source["properties"].items() if k != "id"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["sources"],
        "properties": {
            "$schema": {"type": "string"},
            "defaults": defaults,
            "sources": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/SourceConfig"}},
        },
        "$defs": {**defs, "SourceConfig": source},
    }
