# 宿主能力适配

## 能力发现

优先使用宿主已暴露的搜索、站点连接器、HTTP、文件和浏览器工具。工具名称不是跨平台标准；用宿主工具目录、MCP schemas、CLI `--help` 或 `doctor` 确认实际能力。把实际参数转换为本技能的逻辑输入，不伪造工具名或凭空调用 MCP。没有 shell 也可执行 SKILL.md 的研究流程，但不能宣称运行了 Python 脚本。

| 逻辑能力 | 优先路径 | 无该能力时 |
|---|---|---|
| 公网搜索 | 宿主搜索 → 已配置搜索 API | `search` 生成链接并明确 links_only |
| 平台文章／帖子／视频 | 已授权站点连接器／公开 API | 公开原页；登录墙记为受限 |
| 公网页面 | `fetch` 或宿主 HTTP | 已允许的正文读取服务 |
| JavaScript 正文 | 宿主浏览器／`fetch --browser` | 报告 js_required |
| 公共 PDF | pypdf／宿主 PDF 工具 | 报告缺依赖或 OCR 需求 |
| 大规模采集 | 官方批量导出／站点 API → Scrapy/Crawlee | 本包有界 crawl |
| 文献元数据 | Crossref／DataCite／DOI CSL | 出版商页面人工核验 |

## 保留原有渠道能力

若发现 `agent-reach`，先读当前安装版本 `--help`，再调用其实际存在的诊断与渠道命令。不要将旧版快照、第三方星标数或另一台机器的 cookies 状态当作当前事实。没有安装时，使用同一平台的现有连接器或公开来源。

若发现 Exa MCP／Firecrawl MCP／mcporter，只使用当前已暴露的 schema／帮助信息。Exa、Jina、Firecrawl 的认证、额度和收费可能变化，先确认当前状态。不要把“存在一个公开 endpoint”解释为“无限免费”。

视频：在 `yt-dlp --help` 确认字幕能力后，优先获取已有字幕和时间戳。自动字幕需要标注；没有字幕时不能把标题／简介当作视频全文。RSS／Atom 能替代持续刷新内容站首页。

Crawl4AI：本包不内嵌固定版本 SDK 包装器。宿主已装时可作为外部适配器使用。当前官方参数区分 `browser_type="chromium"` 与 `browser_mode="builtin"`；builtin 模式也依赖浏览器／CDP，不能称为无浏览器 HTTP 层。

## WorkBuddy／其他智能体接入

把完整 `web-harvest-pro/` 目录放入该宿主识别的 skills 目录；以其实际导入流程为准。本技能不修改宿主全局配置。保留 SKILL.md、scripts、references、assets 的相对路径。

通过宿主 shell 使用当前有效 Python，传入完整脚本路径及逐个参数。读取 stdout 的一个 JSON 对象；stderr 是进度。不要拼接网页文字到 shell，不使用 `shell=True`。宿主原生搜索结果可先转成搜索结果 JSON，再用 `fuse` 合并。

`agents/openai.yaml` 是可选的 OpenAI 界面信息，其他智能体可忽略。它不负责安装 MCP、浏览器或依赖。SKILL.md 是通用指令入口，Python CLI 是可选执行入口。

仅用 Python 标准库也可运行 doctor、搜索链接规划、HTTP、HTML／JSON／CSV／RSS／站点地图、批量、爬取、融合和元数据核验。按需求安装扩展，勿一次装齐：

```bash
python -m pip install trafilatura beautifulsoup4 pypdf
python -m pip install playwright
python -m playwright install chromium
```

使用与执行脚本相同的解释器。先在隔离环境安装、做样本，再记录实际版本。核心验收测试无需这些扩展；浏览器和全文 PDF 能力取决于可选依赖。

官方说明（核对日期 2026-10-04）：

- [Playwright Python](https://playwright.dev/python/docs/intro)
- [Crawl4AI 配置](https://docs.crawl4ai.com/api/parameters/)
- [Trafilatura Python 用法](https://trafilatura.readthedocs.io/en/latest/usage-python.html)
