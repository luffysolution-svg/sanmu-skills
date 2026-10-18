# sanmu-paper-search

**从课题、论文或作者出发，找到可核查、可阅读的文献。**

**简体中文** · [English](README.en.md) · [返回合集](../../README.md)

主题检索、作者作品、参考/施引/相似论文、跨来源去重、全文位置和 Zotero 收藏匹配。日常找几篇论文可以直接给候选；开题或扩展检索才记录完整协议。核心 CLI 为 Python 3.10+ 标准库实现，无强制科研服务或模型依赖。

## 让 AI 助手安装（推荐）

复制给正在使用的助手：

```text
请帮我安装并配置 sanmu-paper-search：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git、Node.js 和 Python 3.10+，执行：
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-search -g
   选择当前助手的用户级安装位置；已有安装先检查本地修改。
2. 阅读技能 SKILL.md 和 README，在技能目录运行：
   python scripts/paper_search.py doctor
   python -m unittest discover -s tests -v
3. 仅检查 OPENALEX_API_KEY、SEMANTIC_SCHOLAR_API_KEY（兼容 S2_API_KEY）、UNPAYWALL_EMAIL、CROSSREF_EMAIL、POLITE_EMAIL 是否配置，不显示值。没有凭证时说明可用范围，确需配置才指向官方注册页。
4. 如需 Zotero，启动桌面端并允许本机应用通信，运行 python scripts/zotero_local.py doctor；独立客户端无需宿主 MCP 或云端 API key，不直接读取数据库或 storage。首次写入前，在已授权范围内运行 doctor --apply，并由我在 Zotero 本地对话框中授权。没有 Zotero 也能使用核心检索。
5. 需要全文时使用 acquire.py，优先匹配开放获取（OA）直链；机构访问支持配置 --institutional-proxy 自动转换校园网 WebVPN/EZProxy 链接，并由助手浏览器工具复用已授权会话下载，再用 acquire --import-file 记录。
6. 在仓库之外的新工作目录做一次小规模公开文献查询，报告逐源状态、安装位置与限制；安装验证不改 Zotero 文献。附件由本机 Zotero 管理和同步 WebDAV，未核对其状态时不宣称同步完成。
```

手动安装也可复制完整技能目录到 `.agents/skills/sanmu-paper-search`。Python 3.10+ 标准库核心无需 `pip install`；PDF 文字层身份核对可选安装 PyMuPDF。

## 使用示例

> 帮我找受阻路易斯酸碱对用于光热协同产氢的相关论文，说明为什么值得读，并查哪些已有全文或 SI。

> 围绕 MatterGen 这篇论文分别找参考文献、后续施引和相似研究，检查哪些已在 Zotero。

> 查 Claudio Zeni 的其他材料生成研究，先确认作者身份，不要混入同名作者。

> 更新去年整理的材料逆向设计文献，列新增和缺失记录，保留旧的筛选结论。

## CLI 快速开始

从技能目录执行；输出使用新目录，放在个人工作区，不放公开仓库：

```bash
python scripts/paper_search.py doctor
python scripts/paper_search.py search "generative inorganic materials design" --sources openalex,semantic-scholar,crossref --limit 5 --out /path/to/work/topic
python scripts/paper_search.py lookup 10.1038/s41586-025-08628-5 --out /path/to/work/seed
python scripts/paper_search.py author "Claudio Zeni" --provider semantic-scholar --out /path/to/work/authors
python scripts/paper_search.py author "Tian Xie" --provider openalex --from-paper 10.1038/s41586-025-08628-5 --out /path/to/work/author-from-paper
python scripts/paper_search.py related 10.1038/s41586-025-08628-5 --relation citations --limit 5 --out /path/to/work/citations
python scripts/paper_search.py oa /path/to/work/seed --out /path/to/work/oa
python scripts/paper_search.py check /path/to/work/seed
# 可选来源仅显式启用；默认仍为 OpenAlex、Semantic Scholar、Crossref
python scripts/paper_search.py search 'all:"crystal diffusion"' --sources arxiv --limit 2 --out /path/to/work/arxiv
python scripts/paper_search.py lookup pmid:31452104 --sources europe-pmc --out /path/to/work/europe-pmc
```

本地 Zotero、获取与入库（从技能目录执行）：

```bash
python scripts/zotero_local.py doctor
# 首次本地写入授权：在 Zotero 对话框中确认
python scripts/zotero_local.py doctor --apply
python scripts/acquire.py --papers /path/to/work/seed --index 1 --out /path/to/work/acquisition
# 如需机构访问：助手浏览器工具完成已授权下载后，导入同一篇的文件
python scripts/acquire.py --papers /path/to/work/seed --index 1 --out /path/to/work/acquisition --backend none --import-file /path/to/work/browser-paper.pdf
python scripts/ingest.py /path/to/work/acquisition/manifest.json
# 完成身份与 SI 关系核验、确认预览后，在授权范围内写入
python scripts/ingest.py /path/to/work/acquisition/manifest.json --apply
```

`ingest` 先校验文件 SHA-256 和身份，再按稳定 ID 查重入库；默认预览，已有题录不自动改写。文字层身份未确认或存在 SI 时，需完成相应人工核验后再使用 `--identity-confirmed` / `--si-confirmed`，见 [CLI 指南](references/cli.md) 与 [本地 Zotero](references/local-zotero.md)。首次本地授权无需云端 API key；附件导入成功只表示本地写入完成，WebDAV 由本机 Zotero 同步，完成状态需另行核对。

Windows 使用实际绝对路径并加引号。作者候选核对后，用 `author --author-id ID --provider ...` 获取作品。`search` 和作者作品支持 `--year-from` / `--year-to`。`--limit` 为每源预算，跨源去重后全部保留；默认不做跨库相关性重排，助手根据实际阅读形成候选短名单。参数与记录格式见 [CLI 指南](references/cli.md)。

## 来源与依赖

| 来源 | 主要用途 | 本地配置 |
| --- | --- | --- |
| OpenAlex | 主题、作者、引文和 OA 位置 | `OPENALEX_API_KEY`，无 key 的能力按官方限制降级 |
| Semantic Scholar | 主题、作者、引文和相似推荐 | `SEMANTIC_SCHOLAR_API_KEY` 或 `S2_API_KEY`；共享匿名池可能限流 |
| Crossref | 题录/DOI核对、补召回 | 可选 `CROSSREF_EMAIL` |
| arXiv | 直接检索预印本、按 arXiv ID lookup | `--sources arxiv` 显式选择，无 key |
| Europe PMC | 直接检索、按 DOI/PMID/PMCID lookup | `--sources europe-pmc` 显式选择，无 key |
| Unpaywall | DOI 对应 OA 位置 | `UNPAYWALL_EMAIL` 或可用联系邮箱回退（含 `POLITE_EMAIL`），不用于关键词搜索 |
| Zotero | 查询收藏、附件与查重写入 | 独立 `zotero_local.py`，无需宿主 MCP 或云端 API key；也可用宿主工具或标准 JSON 导出 |

`search` / `lookup` 默认保留原三源，新增来源不会增加默认请求；`related` 仅支持 OpenAlex 与 Semantic Scholar，其他来源明确报告 `unsupported`。arXiv 预印本与正式 DOI 保留版本关系；免费可读不等于开放许可。

核心检索独立可用，不捆绑凭证。获取默认采用标准库直链与合规 OA，剥离外部第三方下载依赖。机构访问支持通过 `--institutional-proxy` 自动生成校内 WebVPN / EZProxy 授权直达链接，并在宿主浏览器中复用已登录会话，下载后用 `acquire.py --import-file` 导入；不承诺取得所有商业付费全文。

## 结果与边界

- 每次运行保存查询、逐源状态、规范题录、脱敏原始响应、可读清单和哈希，明确失败、空结果、截断及未执行。
- 稳定 ID 对齐，保留字段冲突和来源。作者先消歧；预印本与正式版保留关系，不按标题直接混成一篇。
- `metadata_only` 和 `abstract` 区分 API 返回的文本层级；筛选依据需实际阅读后记录。找到地址不等于下载，下载不等于读过。
- 直接公开 PDF 仍可用 `scripts/fetch_pdf.py` 获取。`acquire.py` 在独立于检索记录的 `manifest.json` 中分别记录主文与 SI 的来源、哈希、身份、尝试和取得状态；出版社 HTML 提取的 SI 仅为候选，核对后用 `--select-si` 显式选择，不将候选当作已取得或宣称 SI 全量覆盖。
- 有界检索不证明查全；系统检索需逐源预算与筛选记录，见 [检索协议](references/protocol.md)。
- 不绕过登录/付费访问、不直接操作 Zotero 数据库。私有 PDF、笔记、批注、凭证与测试运行不公开提交。

全文取得后，可选交给 [sanmu-doc2md](../sanmu-doc2md/README.md) 转换，交给 [sanmu-paper-reader](../sanmu-paper-reader/README.md) 精读。两者未安装时仍能交付文献清单、来源与材料。

## 验证与设计参考

```bash
python -m unittest discover -s tests -v
```

离线测试使用模拟响应，覆盖字段归一化、身份合并、错误状态、输出保护和哈希；在线可用性需另做小规模实测。真实 Zotero 测试数据保留在本地工作区。

设计参考：[K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup)、[research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search)、[OmniScholar](https://github.com/luffysolution-svg/omnischolar)、[paper-search-pro](https://github.com/O0000-code/paper-search-pro)、[easyseek](https://github.com/Rimagination/easyseek)。检索与获取核心独立实现。按需阅读：[检索策略](references/retrieval.md) · [获取与交接](references/handoff.md) · [本地 Zotero](references/local-zotero.md)。

采用合集 [MIT 许可证](../../LICENSE)。数据库内容、论文、第三方代码与服务各自的许可继续适用。
