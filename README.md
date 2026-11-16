# news-agent

Crawl mọi bài đăng trong một ngày từ các báo điện tử Việt Nam bằng Scrapy, bóc tách nội dung, tóm tắt, phân loại theo chủ đề và xuất ra một file JSON.

Mặc định có 3 nguồn: VnExpress, Tuổi Trẻ, Thanh Niên. Thêm báo mới chỉ cần sửa `config/sources.json`.

## Cài đặt

Cần Python ≥ 3.12 và [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run news-agent --version
```

Chỉ khi có nguồn đặt `"render_js": true` mới cần cài thêm trình duyệt cho Playwright:

```bash
uv run playwright install chromium
```

## Sử dụng

```bash
uv run news-agent run                              # mọi nguồn đang bật, ngày hôm nay (giờ VN)
uv run news-agent run -d 2026-10-27                # một ngày cụ thể
uv run news-agent run -s vnexpress -s tuoitre      # chỉ một số nguồn
uv run news-agent run -n 5                         # tối đa 5 bài mỗi nguồn (để chạy thử)
uv run news-agent run --json > out.json            # in JSON ra stdout thay vì bảng
uv run news-agent sources                          # liệt kê nguồn và kiểm tra config
uv run news-agent schema > config/sources.schema.json   # sinh lại JSON Schema
```


| Tùy chọn của `run` | Mặc định                                    | Ý nghĩa                                          |
| ----------------------- | ------------------------------------------------ | ---------------------------------------------------- |
| `-c, --config-dir`    | `config` (hoặc biến `NEWS_AGENT_CONFIG_DIR`) | Thư mục chứa `sources.json`, `topics.json`      |
| `-d, --date`          | hôm nay                                       | Ngày cần lấy, dạng `YYYY-MM-DD`, theo giờ VN  |
| `-s, --source`        | mọi nguồn đang bật                         | Chỉ crawl nguồn này (dùng nhiều lần được) |
| `-n, --limit`         | không giới hạn                              | Tối đa N bài mỗi nguồn                        |
| `-o, --output-dir`    | `data/runs`                                    | Nơi ghi `run-<ngày>_<giờ>.json`                 |
| `--json`              | tắt                                           | In JSON ra stdout                                  |
| `-l, --log-level`     | `WARNING`                                      | Mức log của Scrapy                               |

Tiến độ và lỗi in ra stderr; kết quả in ra stdout. Lệnh trả mã thoát `1` nếu có nguồn bị crash hoặc không tìm được link bài nào.

## Cách hoạt động

```
tìm link → lọc theo ngày → tải bài → bóc tách → tóm tắt → phân loại → JSON
```

1. **Tìm link** ([discovery.py](src/news_agent/discovery.py), [spider.py](src/news_agent/spider.py)): không cần khai báo URL chuyên mục. Thử lần lượt và dừng ở bước đầu tiên có kết quả:

   1. Google News sitemap khai báo trong `robots.txt`
   2. Mọi RSS trên trang chủ và trang `/rss`
   3. Link bài trên trang chủ (chưa biết ngày, tải sau cùng)

   Link có ngày đăng ngoài khoảng `[00:00, 24:00)` của ngày cần lấy bị bỏ trước khi tải. URL trùng nhau được gộp.
2. **Bóc tách** ([extract.py](src/news_agent/extract.py)): mỗi trường lấy theo thứ tự `selectors` của nguồn trong `sources.json` → JSON-LD `NewsArticle` → thẻ meta OpenGraph → trafilatura (cho nội dung). Vì vậy báo không khai báo selector nào vẫn chạy được.
3. **Tóm tắt** ([summarize.py](src/news_agent/summarize.py)): tóm tắt trích xuất 50–70 từ. Câu được chấm điểm bằng TextRank trên TF-IDF, cộng điểm vị trí trong bài và độ giống tiêu đề; câu trùng ý bị loại. Không dùng LLM.
4. **Phân loại** ([classify.py](src/news_agent/classify.py)): dừng ở luật đầu tiên khớp:


   | Luật                                                 | Độ tin cậy |
   | ------------------------------------------------------- | --------------- |
   | `category_map` riêng của nguồn                     | 1.0           |
   | Chuyên mục gốc trùng tên trong `topics.json`     | 0.9           |
   | Chuyên mục gốc chứa một tên trong `topics.json` | 0.8           |
   | Từ khóa (tiêu đề ×2, sapo ×1)                  | ≤ 0.8        |

   Dưới `min_confidence` (mặc định 0.45) thì `topic = null`.

Crawl lịch sự: tuân thủ `robots.txt`, mỗi báo chỉ một request một lúc, giãn cách theo `rate_limit_seconds` (tối thiểu 1 giây), retry 3 lần, timeout 20 giây.

## Cấu hình

### `config/sources.json`

```json
{
  "$schema": "./sources.schema.json",
  "defaults": { "enabled": true, "rate_limit_seconds": 1.5 },
  "sources": [
    { "id": "laodong", "name": "Lao Động", "base_url": "https://laodong.vn" },
    {
      "id": "thanhnien",
      "name": "Thanh Niên",
      "base_url": "https://thanhnien.vn",
      "selectors": {
        "title": "h1.detail-title",
        "content": "div[data-role=content]",
        "remove": [".relate-container"]
      }
    }
  ]
}
```


| Khóa                | Mặc định | Ý nghĩa                                                       |
| ---------------------- | ------------- | ----------------------------------------------------------------- |
| `id`                 | bắt buộc  | Chữ thường, số, `-`/`_`; không trùng                      |
| `name`               | bắt buộc  | Tên hiển thị                                                 |
| `base_url`           | bắt buộc  | Trang chủ của báo                                            |
| `enabled`            | `true`      | Có chạy khi không truyền `-s`                               |
| `rate_limit_seconds` | `1.5`       | Giãn cách giữa hai request tới báo này (≥ 1)             |
| `render_js`          | `false`     | Tải trang bài bằng trình duyệt (Playwright)                |
| `feeds`              | `[]`        | RSS/sitemap bổ sung ngoài phần tự tìm                      |
| `category_map`       | `{}`        | Chuyên mục gốc → topic, ưu tiên cao nhất khi phân loại |
| `selectors`          | `{}`        | CSS selector riêng (xem dưới); bỏ trống = tự bóc tách   |

`selectors` có các khóa tùy chọn `title`, `sapo`, `content`, `author`, `published`, `category` (CSS selector, lấy phần tử khớp đầu tiên) và `remove` (danh sách selector rác cần bỏ khỏi thân bài, như tin liên quan, quảng cáo). Trường nào bỏ trống, hoặc selector không khớp do báo đổi giao diện, sẽ tự lấy từ JSON-LD, meta và trafilatura.

`defaults` được gộp vào từng nguồn. Khóa lạ hoặc sai kiểu đều bị báo lỗi; mọi lỗi được gom lại và in một lần. Kiểm tra bằng `news-agent sources`.

### `config/topics.json`

```json
{
  "min_confidence": 0.45,
  "topics": {
    "THE_THAO": { "categories": ["Thể thao", "Bóng đá"], "keywords": ["world cup", "hlv"] }
  }
}
```

Có sẵn 8 topic: `THOI_SU`, `THE_GIOI`, `KINH_TE`, `PHAP_LUAT`, `THE_THAO`, `GIAI_TRI`, `CONG_NGHE`, `DOI_SONG`. So khớp không phân biệt hoa/thường và kiểu bỏ dấu (`Văn hoá` = `văn hóa`), từ khóa khớp trọn cụm (`chip` ≠ `chipset`). Một chuyên mục chỉ được thuộc một topic.

### Thêm một báo mới

Không cần sửa code:

1. Thêm `{ "id": ..., "name": ..., "base_url": ... }` vào `sources`.
2. Chạy thử: `uv run news-agent run -s <id> -n 5`, rồi xem file JSON.
3. Chỉ khi kết quả thiếu trường hoặc nội dung lẫn rác mới cần thêm khối `selectors` cho trường đó.

Hai trường hợp phần tự động không xử lý được:

- **Không tìm được link** (`discovered = 0`): báo không có Google News sitemap hay RSS, và URL bài không kết thúc bằng `-<mã số ≥ 6 chữ số>.htm(l)`. Khai báo RSS/sitemap của báo trong `feeds`.
- **Trang chặn bot** (trả về trang kiểm tra bằng JavaScript thay cho nội dung): crawler chưa hỗ trợ.

## Đầu ra

Mỗi lần chạy ghi một file `data/runs/run-YYYY-MM-DD_HH-MM-SS.json` (giờ VN; không ghi đè file cũ).

```jsonc
{
  "format_version": 3,
  "generated_at": "2026-10-27T14:36:57+00:00",
  "window": { "start": "2026-10-27T00:00:00+07:00", "end": "2026-10-28T00:00:00+07:00" },
  "total_articles": 815,
  "articles": [                       // mọi nguồn gộp chung, mới nhất trước
    {
      "id": "…", "source_id": "thanhnien",
      "url": "…", "canonical_url": "…",
      "title": "…", "sapo": "…", "summary": "…", "content": "…",
      "author": "…", "source_category": "Thể thao", "image_url": "…",
      "topic": "THE_THAO", "topic_confidence": 0.9,
      "published_at": "…", "crawled_at": "…", "content_hash": "sha256…"
    }
  ],
  "runs": [                           // thống kê từng nguồn
    {
      "source_id": "vnexpress", "status": "success",   // success | partial | failed
      "duration_seconds": 408.7,
      "stats": { "discovered": 1247, "out_of_window": 1002, "duplicate_url": 75, "new": 243,
                 "parse_failed": 0, "errors": 0, "skipped_robots": 0, "unclassified": 5 },
      "failures": [ { "url": "…", "reason": "…" } ]
    }
  ],
  "crashed_sources": []
}
```

`status` là `failed` khi không tìm được link nào, `partial` khi hơn nửa số bài không bóc tách được.

## Cấu trúc mã nguồn

```
src/news_agent/
├── cli.py         # lệnh run / sources / schema (Typer + Rich)
├── config.py      # đọc và kiểm tra sources.json, topics.json (Pydantic)
├── crawl.py       # cấu hình và chạy Scrapy
├── spider.py      # NewsSpider: tìm link → tải → bóc tách → phân loại
├── discovery.py   # đọc robots.txt, sitemap, RSS, link trang chủ
├── extract.py     # bóc tách bài (selectors → JSON-LD → meta → trafilatura)
├── summarize.py   # tóm tắt trích xuất
├── classify.py    # phân loại topic
├── text.py        # chuẩn hóa văn bản, URL, ngày giờ
├── models.py      # Article, SourceRun, TimeWindow
└── export.py      # ghi JSON
```

## Phát triển

```bash
uv run ruff check . && uv run ruff format .
uv run mypy
uv run pytest
```
