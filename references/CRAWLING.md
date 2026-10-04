# 抓取、恢复与质量门

## 选择入口

- 单页：`fetch`。静态正文直接提取，默认不启动浏览器。
- 显式 URL 列表：`batch`；跨域并发，按源站请求起始时间限速。一次最多 1000 个去重 URL。
- 公共站点有界探索：`crawl --seed ...`；BFS、种子主机范围、深度和页面上限、过滤与 SQLite 检查点。
- RSS／站点地图／列表页发现链接：`discover URL`；嵌套站点地图有数量上限，仍限制种子主机。
- 数万页／长期更新：先用官方 bulk、增量 API；若仍需抓取，转宿主已经配置的 Scrapy／Crawlee 等队列系统。

本包 crawl 是轻量有界采集器，按序抓取方便审计，不等于分布式爬虫。batch 才提供线程并发。没有浏览器池、自动点击无限滚动或通用字段学习器；这些任务按 ADAPTERS.md 使用宿主能力。

## 小样本验证

先取 1–5 页，检查标题、正文、日期和任务必需字段。翻页任务比较相邻两页 URL／主要记录，确保真的前进；用唯一 ID 或内容哈希检查重复。遇到缺字段、验证码、循环分页、严重乱码就修复样本，不直接扩大规模。

列表页／文档页若误选正文，可用 `--selector` 显式 CSS 选择器（需要 beautifulsoup4）。选择不到会返回 selector_not_found，不当作成功。链接发现与正文提取分别输出，不能把正文里的链接当作完整站点链接清单。

## 状态与升级

| quality／error_kind | 动作 |
|---|---|
| complete 且正文非空 | 接受为“已读取”；再评估字段与论断相关性 |
| js_required | 试取单页 `fetch --browser` |
| challenge／HTTP 401/403 | 记录受限；公开替代源或现有授权连接器 |
| robots_denied／robots_unavailable | 停止该路径／延期该站点 |
| HTTP 429／预算耗尽 | 保留检查点；降低负载或稍后恢复 |
| selector_not_found／empty | 检查页型、选择器、分页和真实响应 |
| pdf_dependency_missing | 安装 pypdf 或使用宿主 PDF 能力 |
| encrypted_pdf／ocr_required | 使用授权密码／OCR 工具；不编造内容 |
| size_limit／pdf_page_limit | 改用有界文件工作流或长文档技能 |

Browser worker 在独立子进程运行；只支持读取，无用户 profile，阻止非 GET/HEAD 和非公网子请求，同源导航。它会使用 page.goto 的实际 HTTP 状态。对 POST 查询型 SPA、登录态内容和复杂交互，使用宿主支持的授权浏览器／API；不要把阻止的交互当成成功。

## 限速、缓存与恢复

默认 batch 总并发 4，同源并发 2，请求起始间隔 0.8 秒；robots crawl-delay 大于默认值时采用更长间隔。Retry-After 支持秒数与 HTTP 日期，并用于后续同域请求。浏览器子请求的速率不与 HTTP 请求共享跨进程队列，故不对批量结果自动逐页启用 browser。

HTTP 缓存仅用于无凭据的公开 GET，默认 TTL 3600 秒。`--refresh` 会重新请求并使用 ETag／Last-Modified 条件验证。每次抓取仍复核 robots；动态库存、价格、新闻等必须按任务选短 TTL／刷新。robots 404/410 按无规则处理，401/403 保守停止，429/5xx／传输失败延期。该策略比 RFC 对部分 4xx 的最低要求更保守，不是完整 RFC 一致性认证。

恢复 crawl 时保持相同种子／include／exclude 和 output；可提高 max-pages。max-pages 是该目录累计已处理页数上限，失败页也计数。修改种子／过滤条件需要新目录。`--retry-failed` 仅重试已记录失败项，勿对拒绝访问路径反复使用。中断后已写入 SQLite 的完成页不用重抓；最后未提交的一页可重新处理。

`frontier_remaining` 和 termination 表明剩余范围；达到深度／页面／前沿上限不能称为“采集全部”。源页面新增内容需要新运行或 refresh；普通 resume 并不自动更新已完成页。

## 数据保真

保留 headings、段落、列表、代码、表格行、JSON-LD、JSON、CSV、RSS／Atom 和 sitemap 链接；JSON-LD 只作为声明数据。合并单元格输出需人工复核。fallback HTMLParser 不等同于完整 HTML5 浏览器，遇到损坏 HTML 使用更强解析器或浏览器。

PDF 最多 500 页。pypdf 读取型任务不渲染图片、不 OCR、不保证科学公式和跨页表格保真。必须检查页码和原图；书籍规模材料使用专业 PDF／长文阅读流程。

官方依据（核对日期 2026-10-04）：[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309)、[Playwright Page](https://playwright.dev/python/docs/api/class-page)。
