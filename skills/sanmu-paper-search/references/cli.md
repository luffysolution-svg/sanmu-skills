# CLI 与记录契约

从本技能目录执行，Python 3.10+，核心不需要 pip 安装。`paper_search.py --help` 和子命令 `--help` 是参数依据。

```bash
python scripts/paper_search.py doctor
python scripts/paper_search.py search "frustrated Lewis pairs photocatalytic hydrogen" --sources openalex,semantic-scholar,crossref --limit 10 --out "/path/to/work/runs/topic"
python scripts/paper_search.py lookup 10.1038/s41586-025-08628-5 --title "A generative model for inorganic materials design" --out "/path/to/work/runs/seed"
python scripts/paper_search.py author "Claudio Zeni" --provider semantic-scholar --out "/path/to/work/runs/authors"
# 同名或译名难消歧时，从种子论文的作者列表直接取稳定作者 ID：
python scripts/paper_search.py author "Tian Xie" --provider openalex --from-paper doi:10.1038/s41586-025-08628-5 --out "/path/to/work/runs/author-from-paper"
# 省略姓名则只列出该论文的作者与 ID 候选，不拉取作品：
python scripts/paper_search.py author --provider openalex --from-paper doi:10.1038/s41586-025-08628-5 --out "/path/to/work/runs/paper-authors"
# 核对候选中的稳定作者 ID 后，再执行：
python scripts/paper_search.py author --author-id AUTHOR_ID --provider semantic-scholar --limit 10 --out "/path/to/work/runs/author-papers"
python scripts/paper_search.py related 10.1038/s41586-025-08628-5 --relation references --sources openalex,semantic-scholar --limit 10 --out "/path/to/work/runs/references"
python scripts/paper_search.py oa "/path/to/work/runs/seed" --out "/path/to/work/runs/oa"
python scripts/paper_search.py merge "/path/to/work/runs/topic" "/path/to/work/runs/references" --zotero "/path/to/work/zotero-items.json" --out "/path/to/work/runs/merged"
python scripts/paper_search.py check "/path/to/work/runs/merged"
python scripts/paper_search.py diff "/path/to/work/runs/seed" "/path/to/work/runs/updated" --out "/path/to/work/runs/diff"
python -m unittest discover -s tests -v
```

输出目录必须新建，防止覆盖用户筛选或已有成果。不要将本地测试输出提交到技能仓库。建议在用户工作目录内使用 `runs/`；测试和私有 Zotero 快照放工作目录。

来源名是 `openalex`、`semantic-scholar`、`crossref`、`arxiv`、`europe-pmc`；默认仍为前三源，两新源用 `--sources` 显式选择。可移植身份格式包括 DOI、`doi:...`、DOI URL、`openalex:W...`、`s2:...`、`pmid:...`、`arxiv:...`。并非所有来源都支持全部 ID，未支持情况需在逐源状态中保留。作者 ID 属于提供者，不能跨库复用。

`search` 和 `author --author-id` 支持 `--year-from 2024 --year-to 2025`；`related` 不支持年份参数，需在关系结果上另做筛选并保留阶段记录。`--limit` 是每源候选预算（1–20），跨源去重后全部保存，默认三源最多 60 条，显式五源最多 100 条；保留源序，不宣称跨库相关性已统一排序。作者单源作品和 `oa` 输入处理同样受该预算约束。已知年份在源端（支持时）和本地过滤，返回的未知年份保留并计数；源端可能不返回缺日期文献，见 `year_filter`。

`author QUERY` 只产生候选；`author --author-id ID` 查询已核对作者的作品，不自动选择同名作者。`author --from-paper ID [NAME]` 先在种子论文作者列表里锚定稳定 ID，再拉取作品；姓名不匹配时该来源记为 `not_found` 并保留全部作者候选，不猜人。`related` 按关系区分接口，不将普通关键词补搜冒充推荐。`oa` 只定位与记录访问状态，不代表已下载。

`summary.md` 是 `papers.json` 的同源精简视图（年份、标题、前三位作者、类型、标识符、证据层级），用于快速浏览；正文判断仍以 `papers.json` 与逐源状态为准。

## 配置（仅环境变量，不写入记录）

检索与元数据来源见 [检索与元数据 API](api-discovery-providers.md)，出版社全文路线见 [出版社获取路线](api-publisher-providers.md)。全文 API 只支持 Elsevier/Wiley；其他出版社走浏览器。持有 Key 不等于具备目标文章的订阅全文权限。

- `S2_API_KEY` / `SEMANTIC_SCHOLAR_API_KEY`：Semantic Scholar 配额。
- `OPENALEX_API_KEY`：OpenAlex 配额与深度翻页；缺失时自动降级为匿名请求。
- `OPENALEX_EMAIL` / `CROSSREF_EMAIL` / `UNPAYWALL_EMAIL` / `POLITE_EMAIL`：Polite Pool 联系邮箱，按此顺序回退；`POLITE_EMAIL` 可一次覆盖三源。OpenAlex 缺少邮箱时仍匿名，深度翻页可能被限流。

`doctor` 只报告这些配置是否存在（布尔值），不显示值。

## 获取限速台账

`publisher_api.py` 与 `webvpn_acquire.py` 共享一份持久化请求台账，默认在 `%LOCALAPPDATA%/sanmu-paper-search/acquisition-state.json`（非 Windows 为 `$XDG_CACHE_HOME/sanmu-paper-search/`）。台账只记录 scope 名、UTC 日计数、滑动窗口时间戳、最后状态和冷却截止时间，不记录 DOI、URL、Key 或 Cookie；写入使用临时文件加原子替换和进程锁。`--state` 指定路径，`--no-state` 关闭持久化，仅用于诊断。

API scope 为 `api:<provider>`，浏览器 scope 为 `webvpn:<host>`。台账执行 UTC 日预算、窗口上限、最小间隔和 `429`/验证码冷却；`--daily-limit` 调整 API 每提供商日预算，`--daily-per-host` 调整浏览器每主机日预算。默认值是本地保护预算，不是出版社合同限额；Wiley 的 60 次/10 分钟按官方限制执行。

## 状态与退出码

- `0`：该命令按其有界范围成功，不意味着全领域查全。
- `1`：部分来源失败等导致部分完成；检查逐来源状态。按约定预算成功取得前 N 条仍可退出 `0`，同时 `truncated=true` 表示来源尚未穷尽。
- `2`：输入或配置问题。
- `3`：所有被请求来源失败。
- `4`：清单完整性检查失败。

获取批量命令另有条目状态 `file_obtained`、`ingested`、`ingested_partial`、`skipped`、`stopped`、`needs_manual`。只有全部条目为已取得、已入库，或可接受的跳过（已有主文、已取得）时才退出 `0`；`ingested_partial`、`stopped`、`needs_manual` 和身份待审都按部分完成处理，逐条检查原因。

stdout 输出结构化 JSON；不将网络错误隐藏为空结果。凭证仅从环境变量读取，不写进运行记录。大规模检索先制定协议；命令行初版有界预算不能冒称系统检索穷尽。

## 文件与交接

运行记录包含 schema 版本、动作/查询、时间与逐源状态；文献记录保留稳定标识符、完整作者、摘要/证据层级、来源、OA 位置和元数据冲突。原始响应保留为脱敏 JSON。哈希清单验证保存产物有没有改变，不验证元数据语义或科学论断。

`merge --zotero` 读取宿主工具在本地保存的 Zotero JSON（普通导出 `items` 或含 `data/key` 的 API 形状）。只比较题录身份并附上库/key和已有附件状态，不上传快照，不读数据库或 storage。宿主输出包含笔记时先裁成题录与附件清单；保留原始快照供本地复查，别公开提交。

直接 PDF 获取另用：

```bash
python scripts/fetch_pdf.py "https://publisher.example/article.pdf" --doi "10.xxxx/real-doi" --title "Exact paper title" --out "/path/to/work/runs/pdf"
```

示例网址与 DOI 是占位符，实际使用从已核对的 OA 位置取得的真实 URL。输出 `paper.pdf`、`acquisition.json`，记录最终 URL、SHA-256、格式检查和身份状态。可选 PyMuPDF 可做首屏题名区的完整 DOI/标题文字层匹配；缺依赖时仍保存 PDF，并明确身份未核验。需要下载附件的任务逐项核对；`acquire.py` 可提取实际页面中的 SI 候选并按选择获取，不据候选列表声称全量取得。

## 获取主文、SI 与浏览器交接

```bash
# 已有题录时选一篇，优先明确 OA 位置自动获取。
python scripts/acquire.py --papers "/path/to/work/runs/oa" --index 1 --out "/path/to/acquisition"
# 读取真实出版社页面，仅发现 SI 候选，不自动下载所有链接。
python scripts/acquire.py --papers "/path/to/work/runs/oa" --index 1 --out "/path/to/acquisition" --publisher-url "https://publisher.example/article"
# 配置校园网 WebVPN / EZProxy 代理前缀，自动生成校内授权直达链接写入 handoff
python scripts/acquire.py --papers "/path/to/work/runs/oa" --index 1 --out "/path/to/acquisition" --institutional-proxy "https://webvpn.univ.edu.cn"
# 使用返回的真实 si-ID 选择文件；页面需再次提供，也可用 --publisher-html 已保存页面。
python scripts/acquire.py --papers "/path/to/work/runs/oa" --index 1 --out "/path/to/acquisition" --publisher-url "https://publisher.example/article" --select-si si-REALID
# 机构授权浏览器取得文件后摄取；来源与版本保留，不自动证明开放许可。
python scripts/acquire.py --papers "/path/to/work/runs/oa" --index 1 --out "/path/to/acquisition" --import-file "/path/to/browser-download.pdf" --source-url "https://publisher.example/article" --version publishedVersion
```

示例网址和 ID 需换为实际结果。新目录保存 `manifest.json`，同一篇可续跑，已有文件先核验哈希并拒绝覆盖。主文和 SI 分开记录文件、来源、许可、版本、身份状态与每次尝试；非 PDF SI 显式指定 `--import-kind supplementary --import-type xlsx` 等类型，不执行压缩包。SI 候选只是实际链接发现，不是完整性或主文关系的验收。

机构获取通过 `handoff` 交给助手浏览器工具，复用已授权会话并保持 CARSI/WebVPN 访问路径；`--authorized-profile` 只表明授权交接，不等于登录有效，也不会启动登录或复制 cookie。下载后用 `--import-file` 恢复。主文取得但已选择的 SI/页面发现失败仍为部分完成，不能只依据文件存在判断整项任务成功。

## 出版社 TDM API 与统一获取入口（可选）

有 Elsevier/Wiley 机构 TDM 凭据时，`webvpn_acquire.py fetch` 默认 `--backend auto`：先调用官方 API，只有 API 明确不支持该 DOI、未配置 Key 或条目没有 DOI 时才进入浏览器。API 的 401/403/429/网络错误不会自动改走浏览器，分别按凭证、内容权限、冷却和网络状态记录；`--backend api` 只跑 API，`--backend browser` 只跑浏览器，便于诊断。

```bash
# API 优先；浏览器只接住 API 不覆盖的条目
python scripts/webvpn_acquire.py fetch --keys KEY1,KEY2 --out "/path/to/acquisition" --backend auto --apply
# 只检查 API 凭据和计划，不联网
python scripts/publisher_api.py fetch --keys KEY1,KEY2 --out "/path/to/api-batch" --dry-run
# 单 DOI；--dry-run 只计划，不请求、不写 paper.pdf
python scripts/publisher_api.py fetch --doi "10.1016/j.example.2026.123456" --out "/path/to/api-batch" --dry-run
```

凭据只从环境变量读取：`ELSEVIER_API_KEY`、`WILEY_TDM_KEY`（兼容 `WILEY_TDM_CLIENT_TOKEN`）。API 只处理 `10.1016/`（Elsevier）与 `10.1002/`、`10.1111/`（Wiley）；其他出版社走通用浏览器 PDF 链接提取。PDF 仍做 header/trailer、SHA-256 和首屏身份检查，产物写 `KEY/paper.pdf` 与 `KEY/manifest.json`。

同一输出目录重跑时，已有 `paper.pdf` 必须与 `manifest.json` 的 SHA-256、条目 DOI 一致才会被视为 `already_obtained`；文件与清单不一致、只有文件没有清单或 DOI 冲突时失败关闭，不覆盖已有字节。`--apply` 只在 `ingest.py` 返回 `success` 时记为 `ingested`，附件部分失败记 `ingested_partial`，身份未匹配保留 `file_obtained` 并等待人工确认。

Wiley 官方当前要求每 10 分钟最多 60 次请求；默认台账按该窗口和 10 秒最小间隔执行，跨运行仍受同一台账约束。Elsevier 默认 2 秒最小间隔和每日 200 次本地预算。`--state` 指定台账路径，`--daily-limit` 覆盖 API 每日预算；`--no-state` 仅用于诊断。其他出版社没有接入本技能的官方 PDF API，统一走浏览器 lane。

## 校园 WebVPN 与浏览器下载（可选）

需要 Playwright 与 Chrome；首次登录由用户在打开的窗口完成，不读取或保存账号密码。`login` 打开入口等待统一身份认证；`fetch` 在 `--backend auto` 下先走 API，未覆盖的条目再进入浏览器。对 Elsevier/Wiley，脚本从图书馆首页点击对应数据库，完成出版社远程访问握手后直连出版社；其他出版社直接打开目标页，使用通用 PDF 链接提取。浏览器 lane 按主机持久化限速：默认 15±5 秒、单次每主机 20 篇、每日每主机 60 篇；`--daily-per-host` 可调。

```bash
python scripts/webvpn_acquire.py login --profile "%LOCALAPPDATA%/sanmu-paper-search/webvpn-profile" --entry "https://lib-scau-edu-cn-s.vpn.scau.edu.cn/"
python scripts/webvpn_acquire.py fetch --keys KEY1,KEY2 --out "/path/to/webvpn-batch" --dry-run
python scripts/webvpn_acquire.py fetch --keys KEY1,KEY2 --out "/path/to/webvpn-batch" --backend browser --delay 15 --jitter 5 --max-per-host 20 --apply
```

浏览器 lane 只对 Elsevier/Wiley 做图书馆入口握手；其他出版社直接打开目标页并使用通用 PDF 链接提取，不维护逐出版社适配。没有稳定 PDF 链接的页面会保留 `needs_manual`。SI 链接按标题和 URL 标记排除，主文与 SI 不混。

只处理本地 Zotero 中已有、但缺少主文 PDF 的条目；已有主文跳过，SI 不算主文。`--apply` 前先完成 `zotero_local.py doctor --apply` 授权；身份未匹配时只下载不入库，人工核对后用 `ingest.py --identity-confirmed`。profile 与 storage state 含会话 Cookie，按本机私有文件保护，勿提交或分享；不扩大到整期、整库或与当前阅读无关的批量下载。

验证码或机构登录墙：`--keep-open` 在当前条目暂停并保留浏览器窗口，检测到验证码/机构登录标记消失后自动重试当前条目；`--challenge-timeout` 控制单次等待。超时或未开 `--keep-open` 时记录 `needs_manual`，并按 `--challenge-cooldown`（默认 86400 秒）写入该主机冷却，避免自动反复撞击。用户手动完成验证后，用 `--manual-cleared` 重跑：它只清除本批次涉及主机的验证码冷却，再按正常限速请求；脚本不自动填写账号、不绕过验证码。CARSI/Shibboleth 走出版社“Access through your institution”页面，由用户完成 SCAU 身份认证；检测到机构登录墙时记录 `institutional_login_required`。会话是否有效以目标文章页能否出现主文 PDF 候选为准，不以图书馆或出版社首页 200 代替。

## 本地 Zotero 与入库

不需要宿主 MCP；先运行 `python scripts/zotero_local.py doctor`。查询、写入、本地授权与附件失败恢复见 [本地 Zotero](local-zotero.md)。写命令默认预览，在已授权范围内加 `--apply`；不将用户缓存凭证和私有运行记录提交到 Git。

```bash
python scripts/zotero_local.py search "MatterGen"
python scripts/zotero_local.py get ITEMKEY1
python scripts/zotero_local.py file PDFKEY12
python scripts/ingest.py "/path/to/acquisition/manifest.json" --parent-key ITEMKEY1
python scripts/ingest.py "/path/to/acquisition/manifest.json" --parent-key ITEMKEY1 --apply
```

用实际 key 替换占位符。大库可给 `ingest` 增加 `--max-items 5000 --max-pages 50`；预算不足时拒绝新建，不能把截断当未收藏。`ingest` 没有 `--parent-key` 时按稳定 ID 查重，已有条目不改写元数据；文件 SHA-256 不符或身份冲突时写前停止。主文未通过文字层身份匹配时，人工核对后加 `--identity-confirmed`；SI 需核对出版社附件与主文关系后加 `--si-confirmed`。两个确认参数代表已完成检查，不用于跳过阅读。输出 `planned`、`success`、`partial` 或 `failed`；正常/预览退出 `0`，部分完成退出 `1`，输入、身份或授权错误退出 `2`。附件上传失败不隐藏，WebDAV 完成状态另行核对。
