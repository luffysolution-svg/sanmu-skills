# API 申请入口、产品权限与本技能配置

官方页面核对日期：2026-10-18。本文记录申请/管理地址与接口用途；没有申请新 Key、查看现有凭证、验证个人订阅权限或执行全文下载。产品与限额可能调整，以官方控制台、响应头及机构协议为准。

## 扩展目录与实现状态

- [检索、开放全文与科研数据](api-discovery-providers.md)：PubMed/PMC、Europe PMC、arXiv、bioRxiv/medRxiv、CORE、OpenAIRE、DOAJ、Zenodo、DataCite、ORCID、Web of Science、Dimensions、Lens。
- [出版社与中文数据库](api-publisher-providers.md)：IEEE、APS、ACS、RSC、Science/AAAS、AIP、IOP、Taylor & Francis、SAGE、Cambridge、Oxford、PLOS、Frontiers、MDPI、ACM，以及万方、CNKI、维普。

扩展目录区分公开接口、申请后接口、合同/项目交付和未确认能力；不代表新增来源已经接入。本技能当前核心适配器是 OpenAlex、Semantic Scholar、Crossref、Unpaywall、arXiv、Europe PMC，另有 Elsevier/Wiley 可选 PDF 获取脚本。下文配置名仅对应实际代码；扩展来源不预设环境变量。本文与两份目录覆盖当前常见科研来源，非全球提供商穷举。

## 快速选择

| 提供商 | 官方申请或管理入口 | 本技能读取的配置 | 能力边界 |
| --- | --- | --- | --- |
| Semantic Scholar | [申请 API Key](https://www.semanticscholar.org/product/api#api-key-form) | `SEMANTIC_SCHOLAR_API_KEY` 或 `S2_API_KEY` | 题录、作者、引文、相似推荐、OA 链接；不授予出版社订阅权限 |
| Elsevier | [My API Key](https://dev.elsevier.com/apikey/manage) | `ELSEVIER_API_KEY` | 当前可选脚本调用 Article Retrieval 请求 PDF；仍须相应产品和内容访问权限 |
| Wiley | [TDM 页面及 Token 申请区](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining) | `WILEY_TDM_KEY` 或 `WILEY_TDM_CLIENT_TOKEN` | TDM PDF；须接受许可并拥有文章访问权限，订阅内容通常要求机构网络 |
| Springer Nature | [开发者门户](https://dev.springernature.com/) / [API Management](https://datasolutions.springernature.com/account/api-management/?redirect-url=api-management/) | 当前未接入 Springer API，`SPRINGER_API_KEY` 尚无脚本读取 | Metadata、Open Access、Full Text TDM 须分清；Full Text TDM 基于专门协议，返回 XML |
| OpenAlex | [注册](https://openalex.org/signup) / [API Key 设置](https://openalex.org/settings/api-key) | `OPENALEX_API_KEY` | 题录、作者、引文、OA 位置；不能据此取得出版社订阅全文 |
| Crossref | [REST API 文档](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)；公开 API 无需注册 | 可选 `CROSSREF_EMAIL`，可回退 `POLITE_EMAIL` | DOI、题录、许可与链接；链接不是全文访问授权 |
| Unpaywall | [REST API 文档](https://unpaywall.org/products/api)；无需 API Key，传真实联系邮箱 | `UNPAYWALL_EMAIL`，可回退可用联系邮箱 | 按 DOI 定位 OA 版本，不用于关键词检索或解锁订阅全文 |

arXiv 与 Europe PMC 的当前检索实现无需 Key，分别见 [arXiv API 手册](https://info.arxiv.org/help/api/user-manual.html) 与 [Europe PMC REST API](https://europepmc.org/RestfulWebService)。Zotero 桌面端本地 API 不需要云端 API Key，见 [本地 Zotero](local-zotero.md)。

## Semantic Scholar：检索与引文

- [官方概览与申请表](https://www.semanticscholar.org/product/api#api-key-form)
- [Academic Graph API 文档](https://api.semanticscholar.org/api-docs/)
- Graph 基地址：`https://api.semanticscholar.org/graph/v1`
- 请求头：`x-api-key`；本技能从上述两个兼容变量读取，不需要同时配置。

填写真实用途并按申请页面完成审核。部分接口可匿名访问，但共享池可能限流，不能把有 Key 理解为无限配额。`openAccessPdf` 是候选链接字段，不表示文件已下载或验证。推荐接口与 Graph 使用各自的接口路径，不把 Graph 基地址当作全部产品的唯一路径。

## Elsevier：区分 Scopus 与 ScienceDirect

- [Developer Portal](https://dev.elsevier.com/) / [My API Key](https://dev.elsevier.com/apikey/manage)
- [ScienceDirect API 产品页](https://dev.elsevier.com/sd_apis.html)
- [API 规范入口](https://dev.elsevier.com/api_docs.html)
- [TDM 条款](https://dev.elsevier.com/tdm_service.html)
- 当前脚本请求：`https://api.elsevier.com/content/article/doi/{doi}?httpAccept=application/pdf`
- 当前脚本请求头：`X-ELS-APIKey`，配置 `ELSEVIER_API_KEY`。

登录开发者门户并按页面创建/管理 Key。申请时说明需要的产品和用途；Scopus 的检索权限不能自动等同于 ScienceDirect 的文章全文权限。官方明确指出，持有 Key 本身不授予内容权限，还需相应 entitlement 或批准；对于机构订阅内容，应确认 API 请求实际处于可识别的机构网络中。浏览器 WebVPN 登录态不自动使独立 API 请求具备校园 IP。

如机构获得官方提供的 institutional token，应按其适用接口文档使用。当前 `publisher_api.py` 只读取 `ELSEVIER_API_KEY`，未实现独立的 `X-ELS-Insttoken` 配置；不要仅添加一个环境变量就声称脚本已支持。

当前 DOI 自动路由仅覆盖 `10.1016/`，这是本地实现范围，不是 Elsevier 产品收录范围。返回 PDF/XML 的能力由接口、内容和授权共同决定；当前脚本只接收通过格式检查的 PDF，不把 XML 伪装为 PDF。

## Wiley：Token 在 TDM 页面领取

- [官方 TDM 说明与 Get a Text and Data Mining Token](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining)
- [Wiley 账号注册](https://onlinelibrary.wiley.com/action/registration)
- [Wiley 官方 TDM Python 客户端](https://github.com/WileyLabs/tdm-client)
- 请求地址：`https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}`
- 请求头：`Wiley-TDM-Client-Token`。

登录 Wiley 账号，在 TDM 页的 Token 区按要求接受许可并获取 Token。将它配置为 `WILEY_TDM_KEY` 或兼容变量 `WILEY_TDM_CLIENT_TOKEN`。Token 用于确认已接受 TDM 条款；文章访问资格仍单独判断，机构订阅文章通常须从机构网络请求。页面返回 404 也可能表示缺少内容权限或网络身份，不能一概判为 DOI 不存在。

官方当前列出的限制同时包括每秒最多 3 篇与每 10 分钟最多 60 次请求，并建议持续请求间隔 10 秒。**现有脚本默认间隔为 2 秒另加 0–1 秒抖动，不足以保证遵守后一个窗口限制。**在持久化限速调度实现前，Wiley 批处理至少显式采用 `--delay 10 --jitter 1`，并遵守其他运行共享的总额度；单进程间隔仍不等于跨运行额度控制。收到 429 后保留任务、遵守 `Retry-After` 或官方退避要求，不立即切到浏览器继续请求同一内容。

官方当前 TDM 服务提供主文 PDF，不提供 SI。SI 需要单独发现、取得并核对与主文的关系。当前本地 DOI 路由仅覆盖 `10.1002/` 与 `10.1111/`，不代表 Wiley 的完整收录范围。

## Springer Nature：先确认产品，再诊断 Key

- [Getting API Access：注册与管理步骤](https://dev.springernature.com/docs/quick-start/api-access/)
- [产品/订阅入口](https://dev.springernature.com/subscription/)
- [API Management：激活产品、查看 Key 与 metric](https://datasolutions.springernature.com/account/api-management/?redirect-url=api-management/)
- [Metadata API](https://dev.springernature.com/docs/api-endpoints/metadata-api/)
- [Full Text API](https://dev.springernature.com/docs/api-endpoints/fulltext-api/)
- [官方支持](https://dev.springernature.com/support/)

| 产品 | 用途 | 能否据此认定可以下载闭源 PDF |
| --- | --- | --- |
| Metadata API（如 `meta/v2`） | 题录元数据 | 不能 |
| Open Access API（`openaccess`） | OA 内容与相关元数据，具体格式依接口 | 不能外推到闭源文章 |
| Full Text TDM（`/xmldata/jats`） | 专门协议所覆盖的文章/章节全文 XML | XML 全文不等于 PDF，也不能外推到全部 Springer/Nature 文章 |

当前官方 Full Text 示例为：

```text
GET https://api.springernature.com/xmldata/jats?q=doi:TARGET_DOI&api_key=YOUR_API_KEY/YOUR-API-METRIC
```

`YOUR_API_KEY` 与 `YOUR-API-METRIC` 均为占位符，应以账户已激活产品的实际配置为准；metric 不是可以从其他产品照抄的固定值。此处仅记录接口，没有为本技能新增调用能力。当前下载脚本尚未读取 Springer 凭证，也不能将 XML 当成主文 PDF 入库。

[官方迁移指南](https://dev.springernature.com/docs/full-text-api-migration-guide/) 说明：旧 Full Text 主机 `spdi.public.springernature.app` 于 2026-08-07 退役，应使用 `api.springernature.com`，路径仍为 `/xmldata/jats`。排查旧 Key 时依次核对：账户/产品是否激活、Key 与 metric 是否配套、端点主机是否正确、协议是否覆盖目标内容、实际错误响应。不要仅凭一次 401/403 就断言 Key 永久不可用，也不要通过探针的一篇成功推断全部订阅权限。

若需要的是出版社版 PDF，仍须用机构实际获准的 PDF 访问路径；Full Text TDM XML 应作为另一种全文产物记录，不能标记为“PDF 已补齐”。

## 配置与故障判断

1. 仅配置本技能实际读取的环境变量；不把 Key 写进 Markdown、命令示例、Git、运行清单或截图。不要输出真实凭证、含 Key 的完整请求 URL 或认证头。
2. `paper_search.py doctor` 当前只检查核心检索配置是否存在，不验证 Elsevier/Wiley 权限，也不检查 Springer 产品。存在不等于有效，有效不等于有目标文章权限。
3. 401/403 应先核对错误正文、产品和权限；Wiley 的部分权限错误可能是 404。429 应等待冷却，不能视作“API 不支持”而立即改走浏览器。
4. 官方资格、产品覆盖与代码实现分开记录；新增配置名和申请页面不代表对应适配器已完成。
5. 不同提供商按各自配额限速；具体上限以账户、接口和官方实时响应为准。每日上限与验证码 24 小时冷却若采用，应明确为本地保护策略，不冒称出版社统一要求。

完整 CLI 参数和现有获取流程见 [CLI 指南](cli.md)。
