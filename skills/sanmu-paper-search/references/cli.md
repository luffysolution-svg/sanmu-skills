# CLI 与记录契约

## 目录

- 学术检索命令与参数（search、lookup、author、related、oa、merge、check、diff）
- Serper（可选）
- Exa（可选）
- Europe PMC
- 配置（环境变量）
- 状态与退出码
- 文件与交接、PDF 获取

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
```

输出使用用户工作目录下的新建目录，例如 `runs/topic-日期/`，保留已有筛选结果。私有 Zotero 快照和运行产物存放在个人工作区。

`search --sources` 的来源名是 `openalex`、`semantic-scholar`、`crossref`；Serper、Exa 与 Europe PMC 分别使用独立的 `serper`、`exa`、`epmc` 子命令，见下文。可移植身份格式包括 DOI、`doi:...`、DOI URL、`openalex:W...`、`s2:...`、`pmid:...`、`pmcid:...`、`arxiv:...`。并非所有来源都支持全部 ID，未支持情况需在逐源状态中保留。作者 ID 属于提供者，不能跨库复用。

`search`、`author --author-id` 和 `author --from-paper` 支持 `--year-from 2024 --year-to 2025`；`related` 不支持年份参数，需在关系结果上另做筛选并保留阶段记录。`--limit` 是每源候选预算（1–20），跨源去重后全部保存，三源最多 60 条；保留源序，不宣称跨库相关性已统一排序。作者单源作品和 `oa` 输入处理同样受该预算约束。年份需为 1000–9999，起始年不得晚于结束年；参数校验在请求前完成。已知年份在源端（支持时）和本地过滤，返回的未知年份保留并计数；源端可能不返回缺日期文献，见 `year_filter`。S2 主题搜索发送 `year` 范围，作者作品发送 `publicationDateOrYear` 范围，两者均再本地复筛；OpenAlex/Crossref 使用各自的服务端日期过滤。

`author QUERY` 只产生候选；`author --author-id ID` 查询已核对作者的作品，不自动选择同名作者。`author --from-paper ID [NAME]` 先在种子论文作者列表里锚定稳定 ID，再拉取作品；仅在姓名唯一匹配时获取作品；不匹配时该来源记为 `not_found`，有多个匹配时保留歧义与全部作者候选，待核对 ID 后继续。`related` 按关系区分接口，不将普通关键词补搜冒充推荐。`oa` 只定位与记录访问状态，不代表已下载。

原有学术命令的 `summary.md` 是 `papers.json` 的同源精简视图（年份、标题、前三位作者、类型、标识符、证据层级），用于快速浏览；正文判断仍以 `papers.json` 与逐源状态为准。S2 题录按响应保留 `url`、`venue`、`journal`、`publication_date`、`citation_count`、`reference_count`；`search` / `lookup` 另请求 `tldr`，其他接口不保证该字段。TLDR 与摘要分别保存，不能据此标为已读。Serper 的摘要展示 `results.json` 中的类型、标题、链接、年份和片段，费用与状态见 `report.md`。

S2 请求按同一 API key 跨进程节流，默认至少间隔 1.05 秒；匿名请求使用共享匿名桶。GET 遇 HTTP 429、502、503、504 最多三次尝试（含首次），等待总预算 10 秒；429 的 `Retry-After` 支持秒数和 HTTP 日期，冷却状态供后续进程遵守。超出等待预算时保留错误，稍后再试。S2 references 被出版社省略时记录 `publisher_restricted`，不把 HTTP 200 或缺少列表解读为零参考文献。

## Serper（可选）

配置 `SERPER_API_KEY` 后，按需选公开接口；不加入 `search --sources`，不自动扩成多接口查询。完整 13 类、环境变量设置、批量与参数边界见 [Serper 指南](serper.md)。

```bash
python scripts/paper_search.py serper --help
python scripts/paper_search.py serper "single atom carbon nitride photocatalytic hydrogen" --type scholar --year-from 2020 --year-to 2022 --limit 5 --out "/path/to/work/runs/scholar"
python scripts/paper_search.py serper "carbon nitride photocatalysis supporting information" --type search --limit 5 --out "/path/to/work/runs/web-search"
```

`--limit 1..20`（默认 10）是每查询跨页合计的本地保留上限，不保证服务端返回数或费用；页码接口可用 `--page 1..100` 和 `--pages 1..3`，reviews 使用游标；lens/webpage/autocomplete 不翻页。Scholar 年份用 `--year-from/to`，不发送 `num`，不与 `scisbd` 混用。`--params` 接收该接口的 JSON 对象；批量用 `--batch FILE`（1–100 个同接口请求对象的 JSON 数组，单次 POST），不与位置查询、`--url`、`--params`、年份或翻页参数混用。

Serper 保存按接口区分的 `results.json`、完整脱敏响应列表 `response.json`，以及运行记录、报告和哈希；`papers.json` 为空数组。普通网页、商品、评论及 Scholar 摘录不自动转成规范论文，不将 Serper 输出目录直接用于论文 `merge` 或 `oa` 流程；先核对稳定 ID，再用 `lookup` 等获取题录。

## Exa（可选）

配置 `EXA_API_KEY` 后，用自然语言补搜 publication。它使用独立子命令，完整配置、实体筛选与费用说明见 [Exa 指南](exa.md)。

```bash
python scripts/paper_search.py exa --help
python scripts/paper_search.py exa "papers on frustrated Lewis pairs for photothermal hydrogen evolution" --type auto --limit 5 --year-from 2023 --out "/path/to/work/runs/exa"
```

`--type auto|lite` 默认 `auto`，`--limit 1..20` 默认 5；年份用 `--year-from/to`，域名用可重复的 `--include-domain` / `--exclude-domain`，`--text-chars 0..10000` 默认 3000，0 不请求文本。接口固定为 `POST /search`、`category: publication`。

`results.json` 保存完整结果列表，`response.json` 保存全部脱敏请求/响应；仅可对齐当前题名的结构化 publication 实体进入 `papers.json`，无 DOI 时仍可作待核验候选，`read=false`。网页文本不写入摘要，网页命中与论文数分别计数，保留 `exa` 来源状态；所有产物纳入哈希清单。费用来自 `costDollars`，缺失记 `unknown`，不是最终账单。HTTP 429/502/503/504 最多三次请求尝试（含首次），网络异常不自动重发。

## Europe PMC（公开接口、全文与补充材料）

无需 API Key，通过独立的 `epmc` 子命令进行预印本、关联数据集与开放获取筛选，并一键获取 JATS 全文 XML 与补充材料 ZIP 包。详细背景与避坑见 [Europe PMC 指南](epmc.md)。

```bash
python scripts/paper_search.py epmc --help
# 1. 筛选含有补充材料与开放获取的论文
python scripts/paper_search.py epmc "photocatalytic hydrogen carbon nitride" --has-suppl --open-access --limit 10 --out "/path/to/work/runs/epmc-search"
# 2. 筛选预印本与关联数据集
python scripts/paper_search.py epmc "carbon nitride" --preprints --has-data --limit 5 --out "/path/to/work/runs/epmc-preprints"
# 3. 精确核验题录与撤稿/勘误关系（通过 DOI、PMID 或 PMCID）
python scripts/paper_search.py epmc 10.1002/adma.202313513 --out "/path/to/work/runs/epmc-seed"
# 4. 下载 JATS 全文 XML 与补充材料解压包（提取 SI PDF 与高清单张原图）
python scripts/paper_search.py epmc PMC12710600 --fetch all --out "/path/to/work/runs/epmc-bundle"
```

`--limit 1..20`（默认 10）为每查询上限；支持 `--year-from/to`、`--sort relevance|cited|date`，以及 `--has-suppl`、`--open-access`、`--has-data`、`--preprints`、`--has-fulltext`。`--fetch none|all|xml|suppl` 控制获取深度。

`papers.json` 保存对齐题录（保留 PMCID、PMID、DOI 与 `commentCorrectionList` 撤稿/勘误关系）；`results.json` 与 `response.json` 保存脱敏响应。当包含 `--fetch` 时，产物另包含 `fulltext.xml`、`fulltext_summary.json`（分节/图表/公式统计）、`supplementary_manifest.json` 与解压目录 `supplementary/`（含 SI 正文 PDF 与高清原图）。所有文件均由 `manifest.json` 计算 SHA-256 哈希防篡改。

## 配置（仅环境变量，不写入记录）

- Europe PMC：公共免凭证开放访问，`doctor` 显示 `europepmc_public_access: true`。
- `SERPER_API_KEY`：可选 Serper 接口，缺失时不执行该服务；安全设置方法见 [Serper 指南](serper.md)。
- `EXA_API_KEY`：可选 Exa publication 检索；安全设置方法见 [Exa 指南](exa.md)。
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

stdout 输出结构化 JSON，网络错误保留为独立状态。凭证从环境变量读取，运行记录保留配置状态。大规模检索先制定协议，按每次执行的预算报告覆盖范围。

## 文件与交接

运行记录包含 schema 版本、动作/查询、时间与逐源状态；文献记录保留稳定标识符、完整作者、摘要/证据层级、来源、OA 位置和元数据冲突。原始响应保留为脱敏 JSON。哈希清单验证保存产物有没有改变，不验证元数据语义或科学论断。

`merge --zotero` 读取宿主工具在本地保存的 Zotero JSON（普通导出 `items` 或含 `data/key` 的 API 形状）。只比较题录身份并附上库/key和已有附件状态，不上传快照，不读数据库或 storage。宿主输出包含笔记时先裁成题录与附件清单；保留原始快照供本地复查，别公开提交。

直接 PDF 获取另用：

```bash
python scripts/fetch_pdf.py "https://publisher.example/article.pdf" --doi "10.xxxx/real-doi" --title "Exact paper title" --out "/path/to/work/runs/pdf"
```

示例网址与 DOI 是占位符，实际使用从已核对的 OA 位置取得的真实 URL。输出 `paper.pdf`、`acquisition.json`，记录最终 URL、SHA-256、格式检查和身份状态。可选 PyMuPDF 可做首屏题名区的完整 DOI/标题文字层匹配；缺依赖时仍保存 PDF，并明确身份未核验。需要下载附件的任务逐项核对，未实现自动 SI 全量发现。
