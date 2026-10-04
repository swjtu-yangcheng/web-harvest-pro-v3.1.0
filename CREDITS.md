# CREDITS — 来源与致谢

本套件整合并重构了以下本机技能与实践经验：

| 来源 | 贡献 | 许可 |
|---|---|---|
| crawler-supervisor（自研） | 路由方法论、证据分级、去重顺序、SECURITY/EVIDENCE/ACADEMIC 参考、《增强智能体网页爬虫能力》1251行调研（TOOLCHAIN） | 自有 |
| web-research-pro（自研） | 环境绑定工具地图、verify_refs.py、CITATION-INTEGRITY（源自《杜绝与纠正"幻觉参考文献"的经验总结》）、AUTHOR-SURVEY 方法 | 自有 |
| multi-search-engine（第三方，v2.1.3） | 16 引擎清单、语言路由、cookie 策略（已固化为 engines.py） | MIT |
| paper-lookup（第三方） | arXiv Atom 四陷阱、Crossref/OpenAlex 配方（浓缩进 academic.py 与 ACADEMIC-API.md） | 见原仓库 |
| Kail的AI工具箱《10个GitHub仓库，免费爬取整个互联网》 | Firecrawl/Crawl4AI/Playwright/Crawlee/Scrapy 三层选型法 | 公众号文章，观点引用 |
| 本机实战（2026-08~09） | 代理502直连回退、Windows Browser.close 噪音、Crawl4AI 冷启动预算、申报书文献核验实战 | — |

第三方工具链（运行时依赖，非本包分发）：
Firecrawl (firecrawl/firecrawl)、Crawl4AI (unclecode/crawl4ai)、
Crawlee (apify/crawlee)、Scrapy (scrapy/scrapy)、Playwright (microsoft/playwright)。

若作为独立仓库发布，请保留本文件中的来源说明。

## v2.0 整合记录（2026-10-04）

| 来源 | 贡献 |
|---|---|
| crawler-power-kit v1.0（自研，本 skill 前身） | 全部爬取层能力：cpk.py 升级阶梯、16引擎、学术API、引文核验、各 references |
| agent-reach（第三方，Panniantong/Agent-Reach，90k★，MIT） | 渠道层能力与调度经验：Exa 免key语义搜索（mcporter）、YouTube字幕（yt-dlp --js-runtimes node）、Jina读页、平台 cookies 体系。skill 文档未并入，运行时路由引用 |
| 安装实战（2026-10-04） | PyPI 同名异物陷阱（jgalea/agent-reach）、SKILL.md 位于 v1.2 分支、npm 全局目录需管理员→workspace+shim 方案、VPN 节点 502 属节点侧 |

**职权声明（v2.1 单包化）**：agent-reach skill 全文已原样并入本包
`references/agent-reach/`（AGENT-REACH.md + 分类 references/），原独立包归档；
渠道命令以该内部文档为权威来源，本文其余部分只做路由不再重复其内容。

## v3.0 重构说明

基于用户提供的 web-harvest v2.1 重写运行核心、检索适配和核验流程。保留上述原始来源与许可记录；旧版路径、版本、星标和安装状态只说明前身来源，不视为当前事实。未再分发旧 agent-reach 文档快照；运行时以实际安装帮助与上游文档为准。第三方库仍为可选运行依赖，不在本包内分发。

## v3.1 本机适配记录（2026-10-04，WorkBuddy 实机）

| 来源 | 贡献 |
|---|---|
| v3.0.0 重构版（ChatGPT 改造） | 全部执行核心：harvest.py 统一 CLI、core.py（robots/限速/缓存/预算）、extract.py、collect.py、browser.py、research.py、academic.py 五要素核验、selftest.py 68 项离线回归 |
| web-harvest v2.1（本机前版） | 渠道层文档回嵌：references/agent-reach/（AGENT-REACH.md/AGENT-REACH_EN.md + 分类 references，frontmatter 已中和）、CHANNELS.md 本机渠道状态；SKILL.md 触发词与渠道优先路由行 |

**v3.1 变更**：文档层嵌入与路由补充；唯一脚本改动为 core.py 版本常量
3.0.0 → 3.1.0（保持 CLI 输出与包版本一致），核心逻辑零改动。
