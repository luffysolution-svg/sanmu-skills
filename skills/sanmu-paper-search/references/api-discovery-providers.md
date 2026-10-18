# 检索与元数据 API

本技能接入六个来源：OpenAlex、Semantic Scholar、Crossref、Unpaywall、arXiv、Europe PMC。它们负责文献发现、题录核对、引文关系和 OA 位置，不提供出版社订阅 PDF。

| 来源 | 用途 | 本地配置 | 官方文档 |
| --- | --- | --- | --- |
| OpenAlex | 主题、作者、引文、OA 位置 | `OPENALEX_API_KEY` 可选；`OPENALEX_EMAIL` 进入礼貌池 | [文档](https://docs.openalex.org/) · [申请 Key](https://openalex.org/signup) |
| Semantic Scholar | 主题、作者、引文、相似推荐 | `SEMANTIC_SCHOLAR_API_KEY` 或 `S2_API_KEY` 可选 | [API 文档](https://api.semanticscholar.org/api-docs/) · [申请](https://www.semanticscholar.org/product/api#api-key-form) |
| Crossref | DOI、题录核对、补召回 | `CROSSREF_EMAIL` 可选，可回退 `POLITE_EMAIL` | [REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) |
| Unpaywall | 按 DOI 定位 OA 版本 | `UNPAYWALL_EMAIL`，可回退 `POLITE_EMAIL` | [API 说明](https://unpaywall.org/products/api) |
| arXiv | 预印本检索与版本 | 无需 Key | [API 手册](https://info.arxiv.org/help/api/user-manual.html) |
| Europe PMC | 生命科学题录、摘要、部分 OA 全文 | 无需 Key | [REST API](https://europepmc.org/RestfulWebService) |

## 共同边界

- **题录不是全文。** 返回标题、作者、摘要或 OA 链接，只说明发现结果；下载和阅读状态必须另行记录。
- **OA 链接不是许可。** 链接可访问、文件可下载、文章可再分发是不同问题；保留来源和许可字段。
- **接口成功不等于查全。** 每个来源有自己的收录范围、分页和限流；保留逐源状态、截断和失败。
- **来源标识不能互换。** DOI、arXiv ID、OpenAlex work ID、Semantic Scholar ID 各有命名空间。

## OpenAlex

- [注册](https://openalex.org/signup)、[API Key 设置](https://openalex.org/settings/api-key)、[认证说明](https://help.openalex.org/api/authentication/)
- 配置：`OPENALEX_API_KEY` 可选；`OPENALEX_EMAIL` 用于礼貌池。
- 用途：主题检索、作者作品、参考文献/施引关系、OA 位置。
- 没有 Key 时可以匿名使用，但深度翻页和批量请求更容易受限。Key 和邮箱只影响请求身份与配额，不授予论文全文权限。

## Semantic Scholar

- [产品与 Key 申请](https://www.semanticscholar.org/product/api#api-key-form)、[Academic Graph API](https://api.semanticscholar.org/api-docs/)
- 配置：`SEMANTIC_SCHOLAR_API_KEY` 或 `S2_API_KEY`，二选一。
- 用途：主题、作者、引文、推荐；`openAccessPdf` 只是候选链接字段。
- 匿名共享池可能限流；推荐接口与 Graph 接口路径不同。Key 不代表无限配额。

## Crossref

- [REST API 文档](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- 配置：`CROSSREF_EMAIL` 可选，也可用 `POLITE_EMAIL` 统一提供联系邮箱。
- 用途：DOI 核对、题录补全、链接与许可字段。
- Crossref 的 link/TDM 字段用于发现，不保证全文可访问，也不能替代出版社授权判断。

## Unpaywall

- [API 说明](https://unpaywall.org/products/api)
- 配置：`UNPAYWALL_EMAIL`，可回退 `POLITE_EMAIL`；使用真实联系邮箱。
- 用途：按 DOI 查询 OA 版本和位置。
- Unpaywall 只负责 OA 定位，不用于关键词检索，也不解锁订阅全文。返回的 OA 位置仍要检查版本、许可和文件是否实际可下载。

## arXiv

- [API 手册](https://info.arxiv.org/help/api/user-manual.html)、[使用条款](https://info.arxiv.org/help/api/tou.html)
- 配置：无需 Key；通过 `--sources arxiv` 显式选择。
- 用途：预印本检索、按 arXiv ID 查询、版本关系。
- 传统 API 要求单连接、请求间隔至少 3 秒；本技能默认按 3 秒间隔执行。预印本不能无标记替代正式发表版。

## Europe PMC

- [REST API](https://europepmc.org/RestfulWebService)、[开发者入口](https://europepmc.org/developers)
- 配置：无需 Key；通过 `--sources europe-pmc` 显式选择。
- 用途：生命科学题录、摘要、可提供的 OA 全文。
- 公开 REST 不等于所有全文都可批量导出；具体文章的 XML/PDF 可用性按子集和许可判断。

## 默认与可选来源

`search`、`lookup`、`oa` 默认使用 OpenAlex、Semantic Scholar、Crossref；arXiv 和 Europe PMC 需要显式加入 `--sources`。`related` 只支持 OpenAlex 与 Semantic Scholar。新增来源不会自动扩大默认请求，也不代表跨库相关性已经统一排序。
