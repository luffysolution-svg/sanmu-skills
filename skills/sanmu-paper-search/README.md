# sanmu-paper-search

**从课题、论文或作者出发，找到可核查、可阅读的文献。**

**简体中文** · [English](README.en.md) · [返回合集](https://github.com/luffysolution-svg/sanmu-skills)

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
3. 仅检查 OPENALEX_API_KEY、SEMANTIC_SCHOLAR_API_KEY（兼容 S2_API_KEY）、UNPAYWALL_EMAIL、CROSSREF_EMAIL 是否配置，不显示值。没有凭证时说明可用范围，确需配置才指向官方注册页。
4. 如需 Zotero，检查宿主插件与本地 API；不要直接读取数据库或 storage。没有 Zotero 也能使用核心检索。
5. 在仓库之外的新工作目录做一次小规模公开文献查询，报告逐源状态、安装位置与限制；不改 Zotero 文献。
```

手动安装也可复制完整技能目录到 `.agents/skills/sanmu-paper-search`。不需要 `pip install`；PDF 文字层身份核对可选安装 PyMuPDF，其他功能不受影响。

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
```

Windows 使用实际绝对路径并加引号。作者候选核对后，用 `author --author-id ID --provider ...` 获取作品。`search` 和作者作品支持 `--year-from` / `--year-to`。`--limit` 为每源预算，跨源去重后全部保留；默认不做跨库相关性重排，助手根据实际阅读形成候选短名单。参数与记录格式见 [CLI 指南](references/cli.md)。

## 来源与依赖

| 来源 | 主要用途 | 本地配置 |
| --- | --- | --- |
| OpenAlex | 主题、作者、引文和 OA 位置 | `OPENALEX_API_KEY`，无 key 的能力按官方限制降级 |
| Semantic Scholar | 主题、作者、引文和相似推荐 | `SEMANTIC_SCHOLAR_API_KEY` 或 `S2_API_KEY`；共享匿名池可能限流 |
| Crossref | 题录/DOI核对、补召回 | 可选 `CROSSREF_EMAIL` |
| Unpaywall | DOI 对应 OA 位置 | `UNPAYWALL_EMAIL`，不用于关键词搜索 |
| Zotero | 检查收藏与已有附件 | 宿主 Zotero 工具，或本地标准 JSON 导出 |

不捆绑凭证，不依赖 OmniScholar、easyseek 或 ScanSci PDF。它们可作额外能力后端，但接入前需核对工具、来源策略、许可和输出；核心检索独立可用。

## 结果与边界

- 每次运行保存查询、逐源状态、规范题录、脱敏原始响应、可读清单和哈希，明确失败、空结果、截断及未执行。
- 稳定 ID 对齐，保留字段冲突和来源。作者先消歧；预印本与正式版保留关系，不按标题直接混成一篇。
- `metadata_only` 和 `abstract` 区分 API 返回的文本层级；筛选依据需实际阅读后记录。找到地址不等于下载，下载不等于读过。
- 直接公开 PDF 可用 `scripts/fetch_pdf.py` 获取并生成来源/哈希/检查记录；SI 需助手从主文附件区逐项核对，首版不自动声称全量获取。
- 有界检索不证明查全；系统检索需逐源预算与筛选记录，见 [检索协议](references/protocol.md)。
- 不绕过登录/付费访问、不直接操作 Zotero 数据库。私有 PDF、笔记、批注、凭证与测试运行不公开提交。

全文取得后，可选交给 [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/tree/main/skills/sanmu-doc2md) 转换，交给 [sanmu-paper-reader](https://github.com/luffysolution-svg/sanmu-skills/tree/main/skills/sanmu-paper-reader) 精读。两者未安装时仍能交付文献清单、来源与材料。

## 验证与设计参考

```bash
python -m unittest discover -s tests -v
```

离线测试使用模拟响应，覆盖字段归一化、身份合并、错误状态、输出保护和哈希；在线可用性需另做小规模实测。真实 Zotero 测试数据保留在本地工作区。

设计参考：[K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup)、[research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search)、[OmniScholar](https://github.com/luffysolution-svg/omnischolar)、[paper-search-pro](https://github.com/O0000-code/paper-search-pro)、[easyseek](https://github.com/Rimagination/easyseek)。核心代码独立实现，不复制这些项目源码。按需阅读：[检索策略](references/retrieval.md) · [获取与交接](references/handoff.md)。

采用 [MIT 许可证](LICENSE)。数据库内容、论文与第三方服务各自的许可继续适用。
