# 出版社与中文数据库：API、TDM 与申请渠道

核对日期：2026-10-18。仅依据官方公开文档；未登录、申请 Key、购买服务或下载论文。本文是扩展能力调研，不代表本技能已实现对应获取器，也不代表用户机构已获得内容/API 权限。现有 Elsevier、Wiley 和 Springer Nature 说明见 [API 主指南](api-access.md)，检索聚合与仓储见 [发现来源目录](api-discovery-providers.md)。

“公开文档/可申请接口”“获准返回全文”“已实现且实测成功”分别记录。政策未给出 Key/格式/速率时标为未确认，不推断服务不存在，不将文章页内部请求当作开放开发者 API。

## 主要出版社一览

| 出版社 | 官方申请/技术入口 | 本次确认的交付方式 | 尚需核实 |
| --- | --- | --- | --- |
| IEEE | [开发者文档](https://developer.ieee.org/docs) / [注册](https://developer.ieee.org/member/register) | 题录 API；OA/收费全文有独立路径 | 申请审核、全文 Token 与内容授权；不将题录 Key 当 PDF 权限 |
| APS | [Harvest](https://harvest.aps.org/) / [API 文档](https://harvest.aps.org/docs/harvest-api) | JSON 题录、XML/PDF/BagIt；OA 可免认证 | 非 OA 的 Key/IP/内容集与格式授权 |
| ACS | [TDM 服务](https://solutions.acs.org/solutions/text-and-data-mining/) / [联系销售](https://solutions.acs.org/contact-us/inquire-about-subscriptions/) | 定制 TDM，列有 JATS/BITS XML 样例 | 报价、范围、交付；自助全文 API 本次未确认 |
| RSC | [官方 TDM/AI 许可页](https://www.rsc.org/publishing/product-information/product-catalogue/text-and-data-mining) | 按项目咨询授权与机器可用内容 | Key、端点、PDF/XML 和数字限额未公开确认 |
| Science / AAAS | [机构许可](https://www.science.org/content/page/institutional-license-agreement) / [联系](https://www.science.org/content/page/contact-us) | 合格机构对限定内容的非商业 TDM 权利 | 实际技术交付、自助 API 与格式未确认 |
| AIP Publishing | [权限入口](https://publishing.aip.org/resources/researchers/rights-and-permissions/permissions/) | 通用权限申请，摘要另有 Crossref 开放记录 | 专门全文 API/交付未确认 |
| IOP | [TDM 政策](https://ioppublishing.org/legal/textanddataminingpolicy/) | XML 元数据、经批准的全文 XML/PDF 交付 | 全文费用、内容覆盖；机器访问另需书面安排 |
| Taylor & Francis | [TDM 政策](https://taylorandfrancis.com/our-policies/textanddatamining/) | 合法访问内容的非商业 TDM，按许可安排 | 自助 API、XML/批量交付和具体速率未确认 |
| SAGE | [期刊 TDM](https://journals.sagepub.com/page/policies/text-and-data-mining) / [TDM-AI 政策](https://www.sagepub.com/tdm-ai-policy) | Crossref 发现题录/许可/全文外链，授权服务另议 | 用途、限速、AI 许可和具体全文格式 |
| Cambridge UP | [TDM 政策](https://www.cambridge.org/core/services/open-research/text-and-data-mining) | Core 网页；许多内容可协商 XML | 官方说明当前未提供 Core 内容 API，大量获取需联系 |
| Oxford UP | [法律与 TDM 条件](https://academic.oup.com/pages/legal-notice) | 按机构合同/文章许可及项目安排 | 开发者 API、交付格式未确认 |
| PLOS | [API 示例](https://api.plos.org/solr/examples/) / [TDM](https://api.plos.org/text-and-data-mining.html) | Solr 题录/摘要；JATS XML/PDF/HTML 与语料 | 旧文档部分链接失效，实时接口尚未实测 |
| Frontiers | [官方 URL/格式说明](https://helpcenter.frontiersin.org/s/article/Article-URLs-and-File-Formats) | HTML、PDF、NLM/JATS XML、EPUB；CORE/FTP 路线 | 年代覆盖、CORE 访问计划与 FTP 审核 |
| MDPI | [OA 政策](https://www.mdpi.com/about/openaccess) / [联系](https://www.mdpi.com/about/contact) | 文章页 OA 内容，按具体许可使用 | 官方开发者网关未证实为论文全文 API |
| ACM | [DL 政策](https://libraries.acm.org/digital-library/policies) / [联系](https://libraries.acm.org/contacts) | 官方说明 2026 年转 OA，Basic 提供 ACM 文章 PDF/摘要及基础 TDM | 本次未确认公开批量 API、Key、具体格式/额度 |

本技能目前未接入上述专用 API。浏览器 lane 已为 Elsevier、Wiley、Springer Nature、ACS、RSC、IEEE、Science、CNKI 增加主机/DOI 映射和主文 PDF 候选提取；这只改进用户已授权会话中的链接识别，不代表 API、Key、批量 TDM 或订阅权限已经获得，也不能绕过登录、验证码或付费墙。通用 OA 直链或浏览器可能获取部分文件，不等于已经实现该出版社的专用 API 适配。

## IEEE：题录、OA 全文和收费全文分开

[文档](https://developer.ieee.org/docs)、[注册](https://developer.ieee.org/member/register)、[条款](https://developer.ieee.org/API_Terms_of_Use2)说明：注册提交申请、经审核发放 Key；资格和用途需满足产品条件，其他情况可能另行许可。

题录 API 包含摘要，可返回订阅范围外的记录；这不表示可取其全文。[收费全文说明](https://developer.ieee.org/Chargeable_Full_Text_Requests)要求销售协助配置 authorization key，交换 Token 后随文章请求提交。[官方 Python SDK 文档](https://developer.ieee.org/Python3_Software_Development_Kit)列有 XML/JSON 全文输出；本次未确认 PDF 交付 API。速率以注册时的 Rate Limits 为准，不假定统一免费额度。

## APS：明确的 PDF/XML 接口，OA 与授权内容分层

[Harvest API](https://harvest.aps.org/docs/harvest-api)基于 `https://harvest.aps.org/v2/journals/articles`，支持 JSON 题录、XML、PDF、BagIt，通过 `Accept` 选择格式。文档明确 OA 文章可免认证获取全文 XML/PDF；非 OA 按 APS 授予的 Key、IP、内容集和格式权限处理，Key 采用 Bearer Token。授权咨询见 [Harvest 入口](https://harvest.aps.org/)的 `help@aps.org`。

每页最多 100 条是分页上限，不是请求/秒配额。列表按响应 `Link` 继续分页，不自行猜下一页地址。accepted fulltext 是独立版本，不能不加区分地标成出版社正式版。

## ACS 与 RSC：先确认项目与交付，不猜接口

[ACS 官方 TDM](https://solutions.acs.org/solutions/text-and-data-mining/)覆盖期刊和图书，列有期刊 JATS XML、图书 BITS XML 样例；费用随范围、期限、频率和交付变化。[销售/申请](https://solutions.acs.org/contact-us/inquire-about-subscriptions/)是明确入口。所查页面没有确认自助全文 API、PDF 交付、统一 Key 或数字限额。

[RSC 官方页](https://www.rsc.org/publishing/product-information/product-catalogue/text-and-data-mining)覆盖期刊、图书、化学数据库，区分 TDM 和 AI 许可，项目开展前可通过页尾 sales/contact 表单咨询。非商业学术 TDM 的条件还涉及当地法律和机构合同。没有确认统一 Key、端点或具体 PDF/XML 交付，不将“machine-ready”自行翻译成 XML API。

## Science / AAAS、AIP、IOP

**Science / AAAS：**[机构许可 Annex A](https://www.science.org/content/page/institutional-license-agreement)为合格非商业订阅机构授权用户，对预定、有限清单提供内部非商业 TDM 条件；商业或其他用途另议。[联系页](https://www.science.org/content/page/contact-us)提供 `scienceonline@aaas.org`。本次未确认公开全文/题录 API、Key 或 XML/PDF 交付。许可中的合理速率、[网站条款](https://www.science.org/content/page/terms-service)的离线浏览器规则不能冒充 API 配额；Science 是 AAAS，不是 Elsevier ScienceDirect。

**AIP Publishing：**[权限页](https://publishing.aip.org/resources/researchers/rights-and-permissions/permissions/)给出 RightsLink 和 `rights@aip.org`，这是通用权限申请，不是已确认的 API 发 Key 流程。[官方 I4OA 公告](https://publishing.aip.org/about/news/i4oa/)说明摘要经 Crossref 开放，不能外推成出版社全文 API。本次未确认专用全文 TDM 端点、交付格式和统一限额。

**IOP：**[2026 年 7 月 TDM 政策](https://ioppublishing.org/legal/textanddataminingpolicy/)说明 XML 元数据含题头、作者、摘要、参考文献，免费供应；全文 XML/PDF 可能收费，XML 对部分旧文、Conference Series 有缺口。联系 `contentsupport@ioppublishing.org`，经批准可 SFTP 或其他约定方式交付；API/结构化馈送等机器访问须书面安排。订阅内容非商业 TDM 要求有效访问资格，商业用途须书面同意，OA 依文章许可。

## Taylor & Francis、SAGE、Cambridge、Oxford

**Taylor & Francis：**[政策](https://taylorandfrancis.com/our-policies/textanddatamining/)允许按适用许可对订阅/OA 内容非商业 TDM，商业用途需询价。技术申请 `support@tandfonline.com`，说明机构与项目。HTML/PDF 网站可读形式不等于公开自动获取 API；XML、Key 和批量技术安排本次未确认。

**SAGE：**[期刊 TDM 说明](https://journals.sagepub.com/page/policies/text-and-data-mining)可用 Crossref 获取题录、许可及全文 URL，全文仍由出版平台控制，须服从访问资格、条款和下载速率。技术支持 `onlinesupport@sagepub.co.uk`。[TDM/AI 政策](https://www.sagepub.com/tdm-ai-policy)对商业用途、AI/LLM/RAG 另有许可要求，商业咨询 `permissions@sagepub.com` 或 CCC RightFind；不把普通非商业 TDM 条件扩展成所有 AI 用途权限。

**Cambridge UP：**[官方政策](https://www.cambridge.org/core/services/open-research/text-and-data-mining)说明非商业 TDM 对合法可访问内容无需另签协议，OA 依 CC 许可；同时明确当前未提供 Core 内容 API。大量获取、自动化技术安排和许多内容可提供的 XML，联系 `openresearch@cambridge.org`，不要自行批量抓普通页面。

**Oxford UP：**[Oxford Academic 法律声明](https://academic.oup.com/pages/legal-notice)说明订阅内容非商业 TDM 按机构合同，OA 依文章许可，商业用途另签协议；联系 `data.mining@oup.com`。本次未确认公开开发者 Key、全文 XML/批量交付 API。申请时先让图书馆确认合同范围。

## PLOS、Frontiers、MDPI、ACM：开放阅读仍需选对技术通道

**PLOS：**[Solr 示例](https://api.plos.org/solr/examples/)列题名、摘要、DOI 检索，默认 XML，可用 `wt=json`；公开示例未要求 Key。[TDM 文档](https://api.plos.org/text-and-data-mining.html)列 JATS XML、PDF、HTML 和语料入口，规模化处理优先语料包，不鼓励逐篇批量抓 HTML/PDF。旧文档部分导航失效，当前服务实时可用性尚未实测，接入前做有界验证。

**Frontiers：**[官方格式页](https://helpcenter.frontiersin.org/s/article/Article-URLs-and-File-Formats)列 HTML、PDF、NLM/JATS XML、EPUB 及 URL 规则。批量获取 2019 年起内容指向 CORE；更早内容可向 `repositories@frontiersin.org`申请 FTP，须说明用途并经审核。CORE 自身计划和许可单独确认，不保证所有文章/格式齐全。

**MDPI：**[OA 政策](https://www.mdpi.com/about/openaccess)允许按 CC BY、注明来源进行文本挖掘，第三方素材另核权利。[开发者网关](https://dev.mdpi.com/)要求 AI Team 提供 Bearer Key，但本次 [ReDoc](https://dev.mdpi.com/redoc)公开列出的主要是 ROR 机构匹配及健康检查，不能据“Public API”名称认定有论文全文接口。论文元数据/全文 API 的资格、端点、XML 批量范围本次未确认，可从 [官方联系页](https://www.mdpi.com/about/contact)询问。

**ACM：**[官方 DL 政策](https://libraries.acm.org/digital-library/policies)说明自 2026-01-01 转 OA，Digital Library Basic 向公众提供 ACM 出版文章 PDF、摘要、基础检索与基础 TDM；第三方 hosted content 不应自动纳入同一范围。公开批量 API、Key 与统一限额本次未确认。技术/数据访问需求走 [DL 联系渠道](https://libraries.acm.org/contacts)，不能用旧订阅印象概括 2026 年后的所有内容。

## 中文数据库

| 来源 | 官方入口 | 本次已确认 | 仍待明确 |
| --- | --- | --- | --- |
| 万方 | [新开放平台](https://ai.wfdata.com/) / [API 广场](https://ai.wfdata.com/skills/api) | 登录自助创建 APPKEY；检索、全文等部分接口可试用 | 单接口订阅、价格、权限、格式和调用限额 |
| CNKI | [数据服务](https://oversea.cnki.net/index/second/digitalService.html) / [联系](https://oversea.cnki.net/index/second/contactUs.html) | 官方商务/定制服务与咨询渠道 | 公开自助文献 API、Key、PDF/CAJ/XML 交付未确认 |
| 维普 | [官网](https://www.cqvip.com/) | 官方咨询渠道；存在受控测试数据平台 | 对外 API 产品、公开申请资格、合同与全文格式未确认 |

**万方：**[原平台迁移公告](https://apps.wanfangdata.com.cn/open)明确指向新平台，应用简化为 APPKEY；企业进入机构账号“我的订阅”，接口文档以新平台为准。旧网关仍兼容不表示可以无授权调用。创建 Key 或试用某接口不代表全库全文免费，也不能把某个网页检索接口视为有权批量获取全部内容。

**CNKI：**本次确认的是海外站数据服务/联系方式，`services@int.cnki.net`、`marketing@int.cnki.net`是海外咨询渠道，不冒充大陆开发者申请入口。商务定制知识门户也不能推断存在公开题录/全文开发者 API。机构 WebVPN/CARSI 的阅读资格不等于商业 API 权限。

**维普：**本次看到维普域名内数据平台说明，但页面自称“内部测试、内部生产阶段”，需申请账号，因此不推荐把测试域名当公开生产接入入口。页面涉及 API KEY、文件审批或 Base64 输出也不能证明外部用户可申请，更不能确认文件必为 PDF。可从官网 `service@cqvip.com`、400-638-5550 询问正式对外产品。

## 选择接入路线

1. API 明确且目标为 OA 的，可先评估 APS；PLOS 先核实旧接口实时状态。IEEE 先申请题录权限，全文资格另问。
2. ACS、RSC、IOP 等先确认可获准内容和交付格式，再决定做 API 适配还是支持离线 XML/文件导入。XML 导入不能标成“PDF 已补齐”。
3. 中文文献优先评估万方官方接口；CNKI/维普保留机构浏览器或正式数据服务申请，不把网页内部端点固化为公开 API。
4. Crossref 的 `link`、TDM 链接和许可信息用于发现，不保证全文访问。见 [Crossref 官方 TDM 文档](https://www.crossref.org/documentation/retrieve-metadata/rest-api/text-and-data-mining-for-researchers/)。
5. 为每条获取结果分别记录产品、授权路径、文件格式、版本、许可和身份检查；未经实测的适配保持候选状态，不新增虚假的配置项或成功率。
