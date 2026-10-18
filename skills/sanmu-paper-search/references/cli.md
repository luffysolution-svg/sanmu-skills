# CLI 与记录契约

从本技能目录执行，Python 3.10+，核心不需要 pip 安装。`paper_search.py --help` 和子命令 `--help` 是参数依据。

```bash
python scripts/paper_search.py doctor
python scripts/paper_search.py search "frustrated Lewis pairs photocatalytic hydrogen" --sources openalex,semantic-scholar,crossref --limit 10 --out "/path/to/work/runs/topic"
python scripts/paper_search.py lookup 10.1038/s41586-025-08628-5 --title "A generative model for inorganic materials design" --out "/path/to/work/runs/seed"
python scripts/paper_search.py author "Claudio Zeni" --provider semantic-scholar --out "/path/to/work/runs/authors"
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

来源名是 `openalex`、`semantic-scholar`、`crossref`。可移植身份格式包括 DOI、`doi:...`、DOI URL、`openalex:W...`、`s2:...`、`pmid:...`、`arxiv:...`。并非所有来源都支持全部 ID，未支持情况需在逐源状态中保留。作者 ID 属于提供者，不能跨库复用。

`search` 和 `author --author-id` 支持 `--year-from 2024 --year-to 2025`；`related` 不支持年份参数，需在关系结果上另做筛选并保留阶段记录。`--limit` 是每源候选预算（1–20），跨源去重后全部保存，三源最多 60 条；保留源序，不宣称跨库相关性已统一排序。作者单源作品和 `oa` 输入处理同样受该预算约束。已知年份在源端（支持时）和本地过滤，返回的未知年份保留并计数；源端可能不返回缺日期文献，见 `year_filter`。

`author QUERY` 只产生候选；`author --author-id ID` 查询已核对作者的作品，不自动选择同名作者。`related` 按关系区分接口，不将普通关键词补搜冒充推荐。`oa` 只定位与记录访问状态，不代表已下载。

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

示例网址与 DOI 是占位符，实际使用从已核对的 OA 位置取得的真实 URL。输出 `paper.pdf`、`acquisition.json`，记录最终 URL、SHA-256、格式检查和身份状态。可选 PyMuPDF 可做首屏题名区的完整 DOI/标题文字层匹配；缺依赖时仍保存 PDF，并明确身份未核验。需要下载附件的任务逐项核对，未实现自动 SI 全量发现。
