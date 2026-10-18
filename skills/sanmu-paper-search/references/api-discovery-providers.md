# 检索、开放全文与科研数据：官方 API 扩展目录

核对日期：2026-10-18。覆盖常见文献发现、引文分析、预印本和开放文件服务，非全球服务穷举。仅核对官方公开文档，未申请账号、Key 或测试个人权限，未执行论文下载。数值限制为文档当日说明，实际执行还须服从账户配额、响应头与服务更新。

**实现状态：**当前技能已接入 OpenAlex、Semantic Scholar、Crossref、Unpaywall、arXiv 和 Europe PMC 的相应检索/发现功能；本页其余服务均为候选来源，尚未接入。arXiv 和 Europe PMC 的服务全文能力不等于本技能已实现对应批量全文接口。下列服务不新增任何环境变量或 CLI 来源名。

现有提供商的申请与变量见 [API 主指南](api-access.md)；出版社及中文商业库见 [出版社目录](api-publisher-providers.md)。

## 快速判断

| 来源 | 官方入口 | 认证/申请 | 适合补充的能力 |
| --- | --- | --- | --- |
| PubMed / PMC | [E-utilities](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html) / [PMC 开发者](https://pmc.ncbi.nlm.nih.gov/tools/developers/) | E-utilities Key 可选，[NCBI 账户](https://www.ncbi.nlm.nih.gov/account/)管理 | 生物医学题录、摘要；符合条件的 PMC 全文 |
| Europe PMC | [REST API](https://europepmc.org/RestfulWebService) | 公共接口无需 Key | 生命科学题录、摘要及可提供的 OA 全文 |
| arXiv | [API 手册](https://info.arxiv.org/help/api/user-manual.html) | 传统查询接口无需 Key | 预印本元数据、摘要与版本 |
| bioRxiv / medRxiv | [官方 API](https://api.biorxiv.org/) | 公共元数据接口未要求 Key | 生物/医学预印本、版本和发表关系 |
| CORE | [服务页](https://core.ac.uk/services/api) / [Key 申请](https://core.ac.uk/api-keys/register) | 有受限免注册访问；Key/更高用量按资格和许可 | 仓储聚合、部分可用全文 |
| OpenAIRE | [Search API](https://graph.openaire.eu/docs/apis/search-api/) / [认证](https://graph.openaire.eu/docs/apis/authentication/) | 匿名可用；认证提升额度 | 研究成果、项目、数据和关联 |
| DOAJ | [官方 API](https://doaj.org/docs/api/) | 公开读取无需 Key；写入凭据另行管理 | OA 期刊和文章题录、全文外链 |
| Zenodo | [开发者文档](https://developers.zenodo.org/) | 公开记录匿名可读；写入和受控操作需 Token | 数据、软件、预印本及开放文件 |
| DataCite | [REST 读取](https://support.datacite.org/docs/rest-api) | Findable DOI 公开读取无需认证 | 数据 DOI、关系、权利与版本元数据 |
| ORCID | [Public API 注册](https://info.orcid.org/documentation/integration-guide/registering-a-public-api-client/) | 匿名/公共客户端/会员分层 | 研究者身份和成果消歧 |
| Web of Science | [Starter](https://developer.clarivate.com/apis/wos-starter) / [Expanded](https://developer.clarivate.com/apis/wos) | 注册 Application 并申请具体产品 Key | 题录核对、引文计数与引文关系 |
| Dimensions | [官方 API 文档](https://docs.dimensions.ai/dsl/api.html) | 机构订阅或获批研究项目 | 出版物、资助及科研分析 |
| Lens | [官方 API](https://docs.api.lens.org/getting-started.html) / [权限申请](https://www.lens.org/lens/user/subscriptions) | 获批计划后管理 Token | 学术文献与专利关联 |

## PubMed 与 PMC：题录和全文分开

- PubMed 的 ESearch/ESummary/EFetch 用于检索、题录与已有摘要；不是出版社订阅 PDF API。见 [NLM E-utilities 指南](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html)。
- Key 在 [NCBI 账户](https://www.ncbi.nlm.nih.gov/account/) Settings → API Key Management 创建/管理。官方说明见 [NCBI API Key 公告](https://ncbiinsights.ncbi.nlm.nih.gov/2017/11/02/new-api-keys-for-the-e-utilities/)：无 Key 默认每 IP 3 次/秒，有 Key 默认每 Key 10 次/秒；更高额度另申请。
- PMC 是全文仓储，PubMed 是题录索引，PMID 与 PMCID 不可互换。PMC 提供自动获取通道，包括 Cloud、OAI-PMH、E-utilities、BioC；应按 [PMC 开发文档](https://pmc.ncbi.nlm.nih.gov/tools/developers/)选择，不对普通页面开展系统性抓取。
- [PMC 开放全文说明](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/)要求按数据子集和文章许可判断可用范围；站内能读不等于可批量导出或再分发，XML、PDF 和附属文件也不保证每篇齐备。

## Europe PMC：无需 Key，但全文有子集边界

[REST 文档](https://europepmc.org/RestfulWebService)和 [开发者通道矩阵](https://europepmc.org/developers)提供元数据、已有摘要、全文链接及部分全文 XML。OA XML/PDF 的可用通道见 [OA 下载说明](https://europepmc.org/downloads/openaccess)，逐篇保留许可。

公共 REST 无需 Key。本次未在所查页面确认统一数字速率，不将其写成无限制。当前技能可显式用 `--sources europe-pmc` 检索，不代表已接入本页描述的全部全文和批量通道。

## arXiv：元数据、PDF/源码和版本分开

[传统 API 手册](https://info.arxiv.org/help/api/user-manual.html)提供 Atom 元数据和摘要，无需 Key；[批量内容说明](https://info.arxiv.org/help/bulk_data.html)另列 PDF/源码通道。传统 API、OAI-PMH、RSS 应遵守 [API 条款](https://info.arxiv.org/help/api/tou.html)的单连接、至少间隔 3 秒要求，并统筹控制下所有机器的请求。

元数据与正文许可不同：正文按文章具体许可和服务条款使用；默认分发许可不自动授予第三方转载/镜像权。当前技能支持 `--sources arxiv`，保留版本号和正式 DOI 关系，不把预印本自动替代正式版。

## bioRxiv / medRxiv：可用来补预印本与正式发表关系

[官方 API](https://api.biorxiv.org/)提供摘要、版本、许可、JATS XML 路径及相关发表信息。公共元数据示例未要求 Key；`details` 文档每页 30 条是分页大小，不是每秒配额，本次未确认统一请求频率上限。

批量全文另见 [bioRxiv TDM](https://www.biorxiv.org/tdm)与 [medRxiv TDM](https://www.medrxiv.org/tdm)：AWS Requester Pays 通道需要 AWS 账户、凭据和费用，可含 PDF、XML及作者提交的补充文件。TDM 仓库提供访问不自动授予转载、衍生内容分发或镜像托管权。

## CORE：确认访问层级和数据许可

- [API 服务页](https://core.ac.uk/services/api)：受限免注册访问、[Key 申请/恢复](https://core.ac.uk/api-keys/register)、付费或符合条件的会员访问并存。
- [v3 文档](https://api.core.ac.uk/docs/v3)：本次详细页面抓取未成功，未据此确认全部接口字段与套餐限额。
- [现行条款](https://core.ac.uk/terms)：使用 API/数据集应核对所需许可，研究项目的适用条件也需确认；CORE 的服务许可不转授底层论文权利。

可返回聚合元数据及部分摘要/全文内容，不保证每条都有原始 PDF。服务页列出“每 10 秒 1 次批量或 5 次单条”的说明，但其适用访问层级未在本次完整确认，不宜直接写成所有账户的统一限额。

## OpenAIRE：项目与成果关系，认证可提升额度

[Search API](https://graph.openaire.eu/docs/apis/search-api/)返回研究成果、项目等元数据和关联，摘要视记录而定，单查询最多 10000 条。它主要是发现接口，文件外链不能当作托管全文或访问授权。

[认证指南](https://graph.openaire.eu/docs/apis/authentication/)提供账户/个人 Token/服务注册路径；个人访问 Token 有效期 1 小时。[配额与许可](https://graph.openaire.eu/docs/apis/terms/)说明匿名 60 次/小时，认证最高 7200 次/小时。Graph 记录的 CC-BY 不自动覆盖链接到的全文文件。

## DOAJ：核查 OA 期刊与元数据

[官方 API 文档](https://doaj.org/docs/api/)说明公共检索、读取不要求 Key；写入主要面向出版商，凭据在 My Account → Settings 管理。返回题录、已有摘要、全文 URL，不托管文章文件。公开说明为 2 次/秒，并允许最多 5 请求的突发排队。

[2026 年 Premium 公告](https://blog.doaj.org/2026/03/03/doajs-new-premium-metadata-services/)主要涉及 OAI-PMH、转储、CSV 的时效；普通 API 仍公开提供最新数据。不能概括成“DOAJ API 全面收费”。

## Zenodo：开放文件与受限文件分开

[开发文档](https://developers.zenodo.org/)提供记录搜索、描述/摘要、许可和文件信息；公开记录元数据匿名可读。开放文件可按其许可获取，受限/禁运文件另需资格。上传、发布等操作的 Token 在 [Applications](https://zenodo.org/account/settings/applications/)管理。

公开文档列出匿名 60 次/分钟、2000 次/小时；认证 100 次/分钟、5000 次/小时，搜索另限 30 次/分钟。[平台原则](https://about.zenodo.org/principles/)区分 CC0 元数据与上传者指定许可的文件；不能将仓储收录当作论文科学质量审核。

## DataCite：公开查 DOI 与写 DOI 是两套权限

[Public REST API](https://support.datacite.org/docs/rest-api)无需认证读取 Findable DOI 元数据，可含摘要、关系、权利；草稿和 Registered DOI 不在相同公开范围。不负责交付所引用的论文或数据文件。

[写入接口](https://support.datacite.org/docs/api)要求 Repository 账户权限。[API Key 管理](https://support.datacite.org/docs/api-keys)可由获准 Repository 凭据通过 `/credentials/api-keys` 管理，普通文献检索者不必申请写入权限。[限流](https://support.datacite.org/docs/rate-limit)按每 IP 每 5 分钟计：未标识 500、提供联系邮箱 1000、认证 3000 次。

## ORCID：用于身份消歧，不是全文来源

[Public API 注册](https://info.orcid.org/documentation/integration-guide/registering-a-public-api-client/)入口为 [Developer Tools](https://orcid.org/developer-tools)。匿名可读公开资料，注册公共客户端使用 Client ID/Secret 获取 Token；[公共客户端条款](https://info.orcid.org/public-client-terms-of-service/)限定适用用途。[Member API](https://info.orcid.org/register-a-client-application-production-member-api/)需要组织会员及凭据，受限读取与写入仍需用户授权。

公开/会员数据范围不同。官方列匿名与注册公共均 12 次/秒，日读取分别 25000 与 100000；Member 24 次/秒且无日用量配额，仍须遵守速率。成果条目是研究者声明或受信来源写入的记录，不提供论文全文，不能仅按姓名合并作者。

## Web of Science：Starter 与 Expanded 分开申请

[开发者门户](https://developer.clarivate.com/)要求先注册 Application，再订阅具体产品并获得 Key。

- [Starter API](https://developer.clarivate.com/apis/wos-starter)：题录核对、链接和按计划提供的被引计数，JSON。公开计划列出 Trial 每秒 1 次/每日 50 次且不返回被引计数；机构成员每秒 5 次/每日 5000 次；机构集成每秒 5 次/每日 20000 次，后者审批面向机构管理人员。应以实际获批计划为准。
- [Expanded API](https://developer.clarivate.com/apis/wos)：付费授权，提供完整条目级元数据、机构、基金、参考/施引等关系，JSON/XML；不同计划按请求数及 Full Records 配额管理。**Full Record 指完整题录，不是论文全文。**

网页订阅与 API 权限不等价，不能承诺 SCAU 已购买某一 API；Starter 不能自动代替 Expanded 的完整引文查询。

## Dimensions：研究分析和系统集成是不同产品

[API 文档](https://docs.dimensions.ai/dsl/api.html)提供 Analytics API 与 CRIS API。Analytics 通过机构订阅，或为 [符合条件的科学计量研究项目](https://www.dimensions.ai/scientometric-research/)申请；一般许可可从 [官方询价](https://www.dimensions.ai/request-a-demo-or-quote/)进入。获准后在 My Account 生成 Key，再交换查询 Token，返回 JSON。官方列每 IP 每分钟 30 次，Token 约 2 小时有效。

Analytics 面向交互分析，不用于直接驱动应用/看板；CRIS 是独立的系统集成产品，应按用途申请。它们提供科研数据与关系，不能视作出版社 PDF 下载权限。

## Lens：学术与专利关联，计划与 Token 配套

[官方入门文档](https://docs.api.lens.org/getting-started.html)要求先在 [账户订阅](https://www.lens.org/lens/user/subscriptions)申请权限，再从 [计划和 Token 管理](https://www.lens.org/lens/user/subscriptions/requested)创建 Token。学术接口包括 `https://api.lens.org/scholarly/search`，支持 JSON 查询；涉及专利时使用相应产品范围。

优先用 `Authorization: Bearer ...`，不把 Token 放进可记录/分享的 URL。额度由获批计划决定，读取响应头中的每分钟剩余请求、每月剩余请求/记录和重试时间。文献与专利元数据、摘要或外链不授予出版社全文权限。

## 接入优先级

对当前材料/化学文献流程，先按明确缺口选择：

1. 跨源 OA/仓储补召回可评估 OpenAIRE、CORE，先核查许可与全文覆盖；不保证增加服务数就提高实际召回。
2. 生命科学方向优先利用已接入的 Europe PMC；需要 PubMed 特有字段或预印本更新时再加 NCBI、bioRxiv/medRxiv。
3. 系统引文分析优先核对机构是否已有 WoS Expanded/Dimensions 资格；Starter、Lens 也需按实际任务判断。
4. DataCite、Zenodo 服务于数据/SI/代码查找，ORCID 服务于作者消歧，DOAJ 服务于 OA 期刊核对，均不替代出版社全文适配。
