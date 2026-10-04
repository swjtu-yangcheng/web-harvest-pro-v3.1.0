# 学术检索与引文核验

## 把三个问题分开

1. 是否检索到候选文献？`paper` 回答发现问题。
2. 题目、作者、期刊、年份、DOI 是否一致？`verify` 回答登记元数据问题。
3. 论文原文是否支持你的具体论断？阅读全文后由智能体回答，核验脚本不能代替。

## 数据源

| 来源 | 用途 | 限制 |
|---|---|---|
| Crossref | 期刊／会议 DOI 和元数据 | 不覆盖全部 DOI，缺失不等于文献虚构 |
| DataCite | 数据集等 DOI；Crossref 无记录时回退 | dataset 等类型不能伪装成期刊文章 |
| DOI CSL content negotiation | 依据 DOI 的结构化书目元数据 | 各注册机构支持情况不同 |
| OpenAlex | 拓展候选、引用图、OA 位置、撤稿标记 | 可能继承 Crossref；不是独立验证真值 |
| arXiv | 预印本候选、版本、摘要 | 必须区分预印本与出版版本 |
| 出版商／发文机关 | 最终题名、作者、版本与原文 | 访问受限时明确记录未完成 |

DOI 输入支持 `10.x/...`、`doi:...` 和 DOI URL。核验保留合法括号等后缀，不通过猜测删除数字。Crossref 查询空结果／404 会继续 DataCite 与 DOI CSL；所有请求出错会记为 UNVERIFIABLE。

Crossref 限速与并发按实际响应及当前官方说明处理。`CROSSREF_MAILTO` 可用于检索的 polite identification，只填写实际邮箱，不使用伪造地址。默认串行核验与同域延迟避免大量重复查找；缓存减少重复 DOI 请求。

截至 2026-10-04 读取的 OpenAlex 官方认证说明允许少量无 key 查询，免费 key 提升预算；用 `OPENALEX_API_KEY` 通过 Authorization header，避免 key 出现在 URL／报告。不要把过时文档中的“永远不需 key／无限免费”写成保证。遇到 401/403/429 标注认证或额度问题，不解释为“文献不存在”。

arXiv 使用 `search_query` 参数和 Atom entry，不读取 feed 自身的 query link 作为论文；HTTP 200 下的 Error entry 仍属于失败。请求间隔至少 3 秒，预印本的版本信息留在 URL 中。

## 严格输入

先由智能体把文字参考文献转换为 JSON，并逐条检查转换准确性。CLI 不根据 GB/T 7714 文本自动猜期刊、作者和标题边界。

```json
[
  {
    "title": "完整且核对过的题名",
    "authors": ["完整作者名一", "完整作者名二"],
    "journal": "完整期刊名",
    "year": 2025,
    "doi": "10.1234/example"
  }
]
```

以上为格式占位，不是可引用文献。期刊缩写／作者缩写／et al 需要先展开或人工确认，不自动当作完全一致。

| 状态 | 含义 | 输出处理 |
|---|---|---|
| VERIFIED_METADATA | 五项提供齐全且登记元数据一致 | 可保留书目信息；仍须检查原文支持关系 |
| PARTIAL | 缺 DOI／作者／期刊／年份／标题等 | 补信息，不列为完整核验 |
| REVIEW | 模糊题名、姓名顺序／缩写、在线／印刷年份差异 | 回出版商／原文逐项确认 |
| MISMATCH | 至少一项已发现明确冲突 | 纠正或替换，禁止猜配 DOI |
| NOT_FOUND | 本轮多个元数据路径均未获取可用记录 | 扩展来源；不是虚构证明 |
| UNVERIFIABLE | 网络、输入或接口等使核验不能完成 | 标注未核验，不能包装成 VERIFIED |

`--secondary` 会记录 OpenAlex 返回值、检查及接口错误；一致只算旁证。论文正文最终核验还要查出版商的更正／撤稿、原始图表与版本；Crossref relation／update-to 和 OpenAlex is_retracted 提供线索而非“未撤稿保证”。

书籍、规范、政策可能无 DOI。改用 ISBN／出版社出版页，或全称／标准号／发文机关／文号／发布日期／现行状态核验；不要强制给这类来源制造 DOI。

官方资料（核对日期 2026-10-04）：

- [Crossref REST 文档](https://github.com/CrossRef/rest-api-doc)
- [DataCite API](https://support.datacite.org/docs/api)
- [DOI Content Negotiation](https://citation.crosscite.org/docs.html)
- [OpenAlex 认证与预算](https://help.openalex.org/api/authentication/)
- [arXiv API 用户手册](https://info.arxiv.org/help/api/user-manual.html)
