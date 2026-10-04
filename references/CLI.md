# 执行契约与命令

## 调用形式

用当前有效 Python 3.10+，脚本路径引用实际安装位置。所有非 help 命令默认 stdout 一个 JSON 对象；`--json` 可放前或后，仅用于旧调用兼容。stderr 是进度，不能与 JSON 混成一个响应。

```bash
python "<skill>/scripts/harvest.py" doctor
python "<skill>/scripts/harvest.py" search "basic magnesium sulfate cement carbonation"
python "<skill>/scripts/harvest.py" search "水泥 碳化" --provider ddg --limit 5
python "<skill>/scripts/harvest.py" fetch "https://example.com/" --output evidence
python "<skill>/scripts/harvest.py" fetch "https://example.com/" --browser --selector "main" --output evidence
python "<skill>/scripts/harvest.py" render "https://example.com/" --screenshot evidence/page.png
python "<skill>/scripts/harvest.py" batch urls.txt --output batch-evidence --concurrency 4
python "<skill>/scripts/harvest.py" discover "https://example.com/sitemap.xml" --limit 100
python "<skill>/scripts/harvest.py" crawl --seed "https://example.com/" --max-pages 30 --max-depth 2 --output crawl-evidence
python "<skill>/scripts/harvest.py" fuse search-runs.json
python "<skill>/scripts/harvest.py" paper "完整论文题名" --provider crossref
python "<skill>/scripts/harvest.py" verify refs.json --secondary --output verification.md
python "<skill>/scripts/harvest.py" selftest
```

`cpk.py` 保留入口；`quick`／`md` 是 fetch 别名，`render` 显式调用浏览器。`md` 不再强制依赖 Crawl4AI。旧 `--engines`、`--time`、verify 的 txt／demo／mailto 不再隐式支持，改为宿主搜索或结构化输入／环境变量。

所有网络命令的公共参数：`--timeout` 15 秒、`--page-budget` 45 秒、`--retries` 2、`--max-bytes` 8388608、`--delay` 0.8 秒、`--per-host` 2、`--proxy-mode env|direct|explicit`、`--cache-dir`、`--cache-ttl` 3600 秒、`--allow-private`。默认公开抓取不能绕过 robots。

URL 列表为 UTF-8、每行一个完整 URL，可用 # 注释。refs.json 为 JSON 对象数组；search-runs.json 为 `[{"provider":"...","results":[{"url":"...","title":"...","snippet":"..."}]}]`。

## 退出码与结果

| 退出码 | 解释 |
|---|---|
| 0 | 命令完成且对应成功条件满足；search links_only 仍仅表示链接规划成功 |
| 1 | 抓取／搜索／核验未全部成功，结果仍可读取 |
| 2 | 输入、环境或执行错误，检查 error_kind |

顶层：`{"ok":true/false,"version":"3.0.0","result":...}`；命令异常含 `error_kind` 和 `message`。抓取结果中的 ok 只代表成功读取并提取，不能替代相关性和事实核验。doctor 顶层 ok 表示标准库可用，联网探测成败看 probes；不是“全网可用”认证。

一次 batch／crawl 产生：

- `records.jsonl`：逐页元数据、状态、结构化数据、哈希、证据路径。
- `pages/<id>.md`：提取正文及来源／时间。
- `manifest.json`：参数、计数、失败类型、重复和范围终止信息。
- `state.sqlite3`：仅 crawl 的持久队列。
- `raw/<id>.bin`：仅显式 --raw 的成功公开响应。

目录中的 url/id 与原始材料相关，不应随意改名后续跑。应用应从记录中的 markdown_path 读取证据。来源记录格式见 assets/source-record.schema.json。

## 最小部署检查

先 `doctor`、`selftest`，再选 1–5 个用户任务目标做真实联网样本。selftest 使用临时 localhost 服务，在测试内部才启用 allow_private；不影响默认公网策略。pytest 等不是必需依赖。浏览器端真实联网测试和 Windows／macOS 实机测试需要在目标环境另跑，不能由 Linux 单元测试替代。
