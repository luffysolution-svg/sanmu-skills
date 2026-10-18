# 全文获取 API：Elsevier 与 Wiley

本技能只接入两个官方 PDF API。其他出版社不维护 API 适配，统一使用浏览器下载，见 [出版社获取路线](api-publisher-providers.md)。检索与元数据来源（OpenAlex、Semantic Scholar、Crossref、Unpaywall、arXiv、Europe PMC）见 [检索与元数据 API](api-discovery-providers.md)。

## 先区分四件事

- **API Key** 是应用身份，不是订阅权限；能调用接口不等于能下载目标文章。
- **题录、摘要、OA 版本、订阅 PDF** 是不同层级；发现一个链接不等于取得文件。
- **浏览器登录态**不会自动传给独立 API 请求；Elsevier/Wiley 的 API 请求从本机直接发出，机构订阅通常要求本机处于校园网或相应授权网络。
- 本技能只接收通过 PDF 文件头和尾检查的响应；XML 或其他全文格式不会被当作 PDF 入库。

## Elsevier

官方入口：

- [Developer Portal](https://dev.elsevier.com/)
- [My API Key](https://dev.elsevier.com/apikey/manage)
- [ScienceDirect API](https://dev.elsevier.com/sd_apis.html)
- [API 文档](https://dev.elsevier.com/api_docs.html)
- [TDM 条款](https://dev.elsevier.com/tdm_service.html)

配置与请求：

- 环境变量：`ELSEVIER_API_KEY`
- 当前请求：`https://api.elsevier.com/content/article/doi/{doi}?httpAccept=application/pdf`
- 请求头：`X-ELS-APIKey`
- DOI 路由：`10.1016/`

申请时说明产品和用途。Scopus 的检索权限不等同于 ScienceDirect 全文权限；机构订阅内容还需要相应的 entitlement。当前脚本没有实现 `X-ELS-Insttoken`，不要通过添加一个环境变量就声称支持机构 token。

`401/403` 先检查 Key、产品和内容权限；`429` 按 `Retry-After` 或本地冷却等待；网络错误保留状态后重试，不会自动改走浏览器请求同一篇文章。

## Wiley

官方入口：

- [TDM 说明与 Token](https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining)
- [Wiley 账号注册](https://onlinelibrary.wiley.com/action/registration)
- [官方 TDM Python 客户端](https://github.com/WileyLabs/tdm-client)

配置与请求：

- 环境变量：`WILEY_TDM_KEY`，兼容 `WILEY_TDM_CLIENT_TOKEN`
- 当前请求：`https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}`
- 请求头：`Wiley-TDM-Client-Token`
- DOI 路由：`10.1002/`、`10.1111/`

在 Wiley 账号的 TDM 页面接受许可并获取 Token。Token 证明已接受 TDM 条款，不代表拥有目标文章权限；机构订阅文章通常要求从机构网络请求。`404` 也可能表示缺少内容权限或网络身份。

官方限制包括每秒最多 3 篇和每 10 分钟最多 60 次请求，并建议请求间隔约 10 秒。本技能的持久化台账按 60 次/10 分钟和 10 秒最小间隔执行。Wiley TDM 提供主文 PDF，不提供 SI。

## 统一运行方式

`webvpn_acquire.py fetch --backend auto` 是统一入口：

1. 有 Elsevier/Wiley Key 时先调用官方 API。
2. 只有 API 不支持该 DOI、未配置 Key 或条目没有 DOI 时，才进入浏览器。
3. `401/403/429`、`not_pdf`、网络错误和普通 HTTP 错误不会静默改走浏览器。
4. `--backend api` 只跑 API；`--backend browser` 只跑浏览器。

`publisher_api.py fetch` 保留为 API-only 诊断入口，也可用 `--doi` 处理单篇。

两个入口共享持久化台账，默认在 `%LOCALAPPDATA%/sanmu-paper-search/acquisition-state.json`，记录 UTC 日计数、滑动窗口、最小间隔和冷却，不记录 DOI、URL、Key 或 Cookie。已有 `paper.pdf` 必须与 `manifest.json` 的 SHA-256 和 DOI 一致才会复用；冲突时失败关闭，不覆盖已有字节。`--apply` 只有在入库返回 `success` 时记 `ingested`，部分附件失败记 `ingested_partial`。

## 其他出版社

其他出版社没有接入本技能的官方 PDF API。它们的浏览器下载路线见 [出版社获取路线](api-publisher-providers.md)；不在此维护逐出版社 API 清单，也不把未接入的接口写成可用能力。
