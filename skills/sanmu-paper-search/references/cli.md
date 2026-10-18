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

- `S2_API_KEY` / `SEMANTIC_SCHOLAR_API_KEY`：Semantic Scholar 配额。
- `OPENALEX_API_KEY`：OpenAlex 配额与深度翻页；缺失时自动降级为匿名请求。
- `OPENALEX_EMAIL` / `CROSSREF_EMAIL` / `UNPAYWALL_EMAIL` / `POLITE_EMAIL`：Polite Pool 联系邮箱，按此顺序回退；`POLITE_EMAIL` 可一次覆盖三源。OpenAlex 缺少邮箱时仍匿名，深度翻页可能被限流。

`doctor` 只报告这些配置是否存在（布尔值），不显示值。

## 状态与退出码

- `0`：该命令按其有界范围成功，不意味着全领域查全。
- `1`：部分来源失败等导致部分完成；检查逐来源状态。按约定预算成功取得前 N 条仍可退出 `0`，同时 `truncated=true` 表示来源尚未穷尽。
- `2`：输入或配置问题。
- `3`：所有被请求来源失败。
- `4`：清单完整性检查失败。

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
