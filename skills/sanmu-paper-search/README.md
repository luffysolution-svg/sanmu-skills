<div align="center">

# sanmu-paper-search

**从课题、论文或作者出发，找到可核查、可阅读的文献。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![No dependencies](https://img.shields.io/badge/core-stdlib%20only-brightgreen.svg)](#来源与配置)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md) · [返回合集](https://github.com/luffysolution-svg/sanmu-skills)

[安装](#安装) · [使用示例](#使用示例) · [命令速查](#命令速查) · [来源与配置](#来源与配置) · [结果与边界](#结果与边界)

</div>

主题检索、作者作品、参考文献 / 施引文献 / 相似论文、跨来源去重、全文和 SI 定位、Zotero 收藏匹配。日常找几篇论文直接给候选；开题或系统检索才记录完整协议。核心 CLI 只用 Python 3.10+ 标准库，不依赖任何科研服务或模型。

## 安装

### 让 AI 助手安装（推荐）

```text
请帮我安装并配置 sanmu-paper-search：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git、Node.js 和 Python 3.10+，执行：
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-search -g
   已有安装时先检查本地修改。
2. 在技能目录运行：
   python scripts/paper_search.py doctor
   python scripts/paper_search.py --help
3. 检查 OPENALEX_API_KEY、SEMANTIC_SCHOLAR_API_KEY（兼容 S2_API_KEY）、UNPAYWALL_EMAIL、CROSSREF_EMAIL，
   以及可选的 SCIVERSE_API_KEY（兼容 SCIVERSE_API_TOKEN）、AI4SCHOLAR_API_KEY、EASYSCHOLAR_SECRET_KEY、SERPER_API_KEY、EXA_API_KEY 是否已配置，不要显示值。Europe PMC 免 Key。
   没有凭证时说明可用范围，确实需要时再给出官方注册页。
4. 需要 Zotero 时检查宿主插件和本地 API；不要直接读数据库或 storage。没有 Zotero 也能检索。
5. 在仓库之外新建一个工作目录，做一次小规模查询，报告各来源状态、安装位置和限制；不要改动 Zotero。
```

### 手动安装

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-search -g
```

也可以把整个技能目录复制到 `~/.agents/skills/sanmu-paper-search`。不需要 `pip install`；想用 PDF 文字层核对论文身份时，可选装 PyMuPDF。

## 使用示例

> 帮我找受阻路易斯酸碱对用于光热协同产氢的相关论文，说明为什么值得读，并查哪些已有全文或 SI。

> 围绕 MatterGen 这篇论文分别找参考文献、后续施引和相似研究，检查哪些已在 Zotero。

> 查 Claudio Zeni 的其他材料生成研究，先确认作者身份，不要混入同名作者。

> 这篇论文有没有被撤稿或发过勘误？顺便把补充材料的原图下载下来。

> 更新去年整理的材料逆向设计文献，列出新增和缺失的记录，保留旧的筛选结论。

## 命令速查

从技能目录执行。`--out` 指向个人工作区里一个新目录，不要放进公开仓库。Windows 上用实际的绝对路径并加引号。

```bash
python scripts/paper_search.py doctor

# 主题、种子论文、作者
python scripts/paper_search.py search "generative inorganic materials design" --sources openalex,semantic-scholar,crossref --sort relevance --with-ranks --limit 5 --out /path/to/work/topic
python scripts/paper_search.py lookup 10.1038/s41586-025-08628-5 --with-ranks --out /path/to/work/seed
python scripts/paper_search.py author "Claudio Zeni" --provider semantic-scholar --out /path/to/work/authors
python scripts/paper_search.py author "Tian Xie" --provider openalex --from-paper 10.1038/s41586-025-08628-5 --topic "materials" --sort cited --out /path/to/work/author-from-paper

# 引文、期刊分区、开放获取、校验
python scripts/paper_search.py related 10.1038/s41586-025-08628-5 --relation citations --with-ranks --limit 5 --out /path/to/work/citations
python scripts/paper_search.py rank "Advanced Materials" "ACS Catalysis" --out /path/to/work/ranks
python scripts/paper_search.py oa /path/to/work/seed --out /path/to/work/oa
python scripts/paper_search.py check /path/to/work/seed
```

- 作者候选核对后，用 `author --author-id ID --provider ...` 取作品（支持 `--topic` 课题方向过滤与 `--sort cited|date`）。
- `search` 支持 `--sort relevance|cited|date` 与 `--year-from` / `--year-to`；加 `--with-ranks` 可用 easyScholar 自动标注期刊分区与影响因子。
- `--limit 1..20` 是每个来源的候选预算，跨源去重后全部保留，不做跨库重排；由助手阅读后形成短名单。

完整参数和记录格式见 [CLI 指南](references/cli.md)。

### SciVerse：全文语义 RAG、原文切片与原图、Paper Schema 复现材料包（可选，需 `SCIVERSE_API_KEY`）

```bash
python scripts/paper_search.py sciverse "How do single-atom Ni cocatalysts enhance photocatalytic H2 evolution on g-C3N4?" --mode semantic-search --semantic-mode quality --limit 5 --out /path/to/work/sciverse-rag
python scripts/paper_search.py sciverse "single atom photocatalyst hydrogen evolution" --mode meta-search --freshness-boost MILD --impact-boost MILD --limit 10 --out /path/to/work/sciverse-meta
python scripts/paper_search.py sciverse "AlphaFold2 protein structure" --mode content --chars 6000 --download-figures --out /path/to/work/sciverse-content
python scripts/paper_search.py sciverse "graph neural network molecular property" --mode schema-materials --goal reproduction --out /path/to/work/sciverse-materials
```

支持结构化检索（`meta-search`：论文/学者/期刊，含布尔检索式、新鲜度/影响力/语言软加权与 FWCI）、自然语言全文片段检索（`semantic-search`：返回原文段落、页码与 Unicode 码点偏移 `offset`）、按 Unicode 码点偏移读取原文 Markdown 并自动下载正文原图（`content --download-figures` / `resource`）、引文关系追踪（`relations`），以及 Paper Schema 结构化论文剖析与复现材料包（`schema-search` / `schema-materials` / `schema-evidence` / `schema-citations` / `schema-in-paper`）。

### AI4Scholar 与 easyScholar：全源检索、科研绘图、自动引文与期刊分区（可选）

```bash
python scripts/paper_search.py ai4scholar "single atom Ni photocatalyst" --mode gs-search --year-from 2023 --limit 10 --with-ranks --out /path/to/work/ai4s-gs
python scripts/paper_search.py ai4scholar "Single-atom Ni/NiOx on g-C3N4 H2 evolution schematic" --mode draw --draw-action smart --model flash --aspect-ratio 16:9 --image-size 2K --out /path/to/work/ai4s-draw
```

配置 `AI4SCHOLAR_API_KEY` 后可使用 Semantic Scholar、Google Scholar、Google Patents、PubMed、Nano Draw 科研绘图/矢量化与 Auto-Cite 自动引文；配置 `EASYSCHOLAR_SECRET_KEY` 后可通过 `rank` 子命令或 `--with-ranks` 标注中科院升级版分区、JCR Q1–Q4、SCI 影响因子、CCF 等级与预警状态。

### Europe PMC：全文、SI 与撤稿核验（免 Key）

```bash
python scripts/paper_search.py epmc "photocatalytic hydrogen" --has-suppl --open-access --limit 5 --out /path/to/work/epmc-search
python scripts/paper_search.py epmc PMC12710600 --fetch all --out /path/to/work/epmc-bundle
```

支持预印本（`--preprints`）、关联数据（`--has-data`）、撤稿与勘误核验；`--fetch all/xml/suppl` 下载 JATS 全文 XML 和补充材料压缩包，安全解压后得到 SI 的 PDF 和正文高清原图。注意它的材料期刊收录不全，不适合作为唯一检索源，见 [Europe PMC 指南](references/epmc.md)。

### Serper：Google Scholar、网页、专利（可选，需 `SERPER_API_KEY`）

```bash
python scripts/paper_search.py serper "single atom carbon nitride photocatalytic hydrogen" --type scholar --year-from 2020 --year-to 2022 --limit 5 --out /path/to/work/scholar
```

共 13 类公开接口，按请求计费。结果保存在 `results.json` / `response.json`，`papers.json` 为空，核对出稳定 ID 后再进入论文流程。配置方法、批量请求和费用见 [Serper 指南](references/serper.md)。

### Exa：用一段话找论文（可选，需 `EXA_API_KEY`）

```bash
python scripts/paper_search.py exa "papers on frustrated Lewis pairs for photothermal hydrogen evolution" --type auto --limit 5 --year-from 2023 --out /path/to/work/exa
```

只把结构化的 publication 实体作为待核验候选写进 `papers.json`，完整结果另存；费用以 API 返回的 `costDollars` 为参考。见 [Exa 指南](references/exa.md)。

## 来源与配置

| 来源 | 用途 | 配置 |
| --- | --- | --- |
| OpenAlex | 主题、作者、引文、OA 位置 | `OPENALEX_API_KEY`；没有时按官方限制降级 |
| Semantic Scholar | 主题、作者、引文、相似推荐 | `SEMANTIC_SCHOLAR_API_KEY` 或 `S2_API_KEY`；匿名共享池可能限流 |
| Crossref | DOI 与题录核对、补充召回 | 可选 `CROSSREF_EMAIL` |
| Europe PMC | 预印本、关联数据、撤稿核验、JATS 全文与 SI 原图包 | 免 Key |
| Unpaywall | DOI 对应的 OA 位置 | `UNPAYWALL_EMAIL`，不用于关键词检索 |
| SciVerse（可选） | 全文语义片段 RAG、结构化文献/学者/期刊检索、原文 Markdown 切片与原图提取、Paper Schema 复现材料包 | `SCIVERSE_API_KEY` 或 `SCIVERSE_API_TOKEN`（兼容 `~/.sciverse/credentials.json`） |
| AI4Scholar（可选） | 高速 S2、Google Scholar、Google Patents、PubMed、Nano Draw 科研绘图与 Auto-Cite 自动引文 | `AI4SCHOLAR_API_KEY` |
| easyScholar（可选） | 中科院升级版分区、JCR Q1–Q4、SCI 影响因子、CCF 与预警状态 | `EASYSCHOLAR_SECRET_KEY` |
| Serper（可选） | Google Scholar、网页、专利等 13 类接口 | `SERPER_API_KEY`，按请求计费 |
| Exa（可选） | 自然语言描述找 publication | `EXA_API_KEY` |
| Zotero | 核对收藏与已有附件 | 宿主 Zotero 工具，或本地标准 JSON 导出 |

`doctor` 只报告每项凭证是否配置，不显示具体值。

## 结果与边界

- **每次运行都留记录**：查询、各来源状态、规范题录、脱敏原始响应、可读清单和哈希；失败、零结果、截断、未执行分开标明。
- **身份对齐靠稳定 ID**：字段冲突保留来源；作者先消歧，种子论文里姓名唯一匹配才取作品；预印本和正式版保留关系，不按标题合并成一篇。
- **限速与重试**：Semantic Scholar 按同一 key 跨进程限速，识别 `Retry-After`；出版社省略参考文献时记为受限状态，不当成零条。
- **找到 ≠ 下载 ≠ 读过**：`metadata_only` / `abstract` 只表示 API 返回的文本层级，筛选依据要实际阅读后才记录。
- **全文和 SI**：公开 PDF 用 `scripts/fetch_pdf.py` 下载并记录来源和哈希；其他出版社的 SI 由助手逐项核对，记录拿到了哪些、缺了哪些。
- **查全有边界**：有限预算的检索不能证明查全；系统检索按[检索协议](references/protocol.md)记录每源预算和筛选过程。
- **不越界**：不绕过登录或付费墙，不直接操作 Zotero 数据库；私人 PDF、笔记、凭证和运行记录不要公开提交。

拿到全文后，可以交给 [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/tree/main/skills/sanmu-doc2md) 转换、[sanmu-paper-reader](https://github.com/luffysolution-svg/sanmu-skills/tree/main/skills/sanmu-paper-reader) 精读；两者没装时，照样交付文献清单、来源和文件。

## 验证与设计参考

安装后先运行 `doctor`，再用上面的示例查几篇文献，并用 `check` 校验输出。开发测试覆盖字段归一化、身份合并、错误状态、输出保护和哈希，在仓库根目录运行：

```bash
python -m unittest discover -s tests/sanmu-paper-search -v
```

设计参考：[K-Dense paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/paper-lookup)、[research-skills / lit-search](https://github.com/luwill/research-skills/tree/main/lit-search)、[OmniScholar](https://github.com/luffysolution-svg/omnischolar)、[paper-search-pro](https://github.com/O0000-code/paper-search-pro)、[easyseek](https://github.com/Rimagination/easyseek)。核心代码独立实现，未复制这些项目的源码。延伸阅读：[首次配置与 API key](references/setup.md) · [检索策略](references/retrieval.md) · [获取与交接](references/handoff.md)。

## 许可证

[MIT](LICENSE)。数据库内容、论文和第三方服务各自的许可继续适用。
