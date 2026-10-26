<div align="center">

# sanmu-skills

**三木的科研 Agent Skills 合集**

文档转换 · 文献精读 · 文献检索：把实际科研中沉淀的工作方法，整理成可安装、可复用的技能。

[![Release](https://img.shields.io/github/v/release/luffysolution-svg/sanmu-skills?label=release)](https://github.com/luffysolution-svg/sanmu-skills/releases/latest)
[![自动化测试](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-standard-orange.svg)](https://agentskills.io)
[![MIT 许可证](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md)

[技能一览](#技能一览) · [安装](#安装) · [使用](#使用) · [参与贡献](#参与贡献)

</div>

## 技能一览

| 技能 | 一句话 | 适合的请求 |
| --- | --- | --- |
| [**sanmu-doc2md**](skills/sanmu-doc2md/README.md) | 把论文、教材、课件转成保留公式、高清图表和页码来源的 Markdown 知识库 | “把这本扫描教材按章转成 Markdown”“论文转 Obsidian 笔记，公式别乱” |
| [**sanmu-paper-reader**](skills/sanmu-paper-reader/README.md) | 精读论文和 SI，每个结论都能追回原文位置和实验条件 | “精读这篇，解释 Fig. 4”“比较这三篇的产氢性能能不能直接比” |
| [**sanmu-paper-search**](skills/sanmu-paper-search/README.md) | 从课题、论文或作者出发找文献，如实记录查了哪里、漏了什么 | “帮我找 FLP 光热产氢的论文”“这篇被撤稿了吗”“下载它的 SI 原图” |

三个技能可以单独用，也可以串起来：**检索** → 拿到 PDF / SI → **转换** → **精读**。任何一个没装，其余两个照常工作。

### sanmu-doc2md

- **按文档类型选路**：教材按章拆分，课件按讲次整理，论文独立成篇并与 SI 双向链接。
- **公式和图表不走样**：全量 KaTeX 预检，图表优先取源文件原图，多面板碎图可回源重裁。
- **错了能追回去**：保留原页页码，所有修正都写进带原值守卫的 repairs 文件，原始 OCR 不动。
- **接上 Zotero**：同步元数据、阅读笔记和 PDF 批注，写入经官方 MCP 执行并回读确认。

### sanmu-paper-reader

- **五种场景**：单篇精读、跨文献比较、跨学科迁移、跨主题组织、知识库问答。
- **证据可追溯**：每个论断记录原文位置、单位和工况，作者结论、你的解读、待验证假设分开写。
- **材料化学检查项**：合成表征、催化电化学、DFT、光谱机理、性能比较，按论文实际内容挑选。
- **按需增强**：图文对照、Mermaid 机理图、复现准备 SOP 草案、Benchmark 表、SI 定向检索。

### sanmu-paper-search

- **多源检索**：OpenAlex、Semantic Scholar、Crossref，可选 AI4Scholar（S2、Google Scholar、专利、PubMed）、Serper 和 Exa 语义补搜。
- **期刊分区与科研辅助**：easyScholar 分区、IF、CCF 与预警标注；AI4Scholar 科研绘图、矢量化和自动引文。
- **引文扩展与作者消歧**：参考文献、施引文献、相似论文分开记录；同名作者用稳定 ID 锚定。
- **全文与 SI**：Europe PMC 免 Key 获取 JATS 全文和补充材料原图包，并核验撤稿与勘误。
- **如实报告**：成功、零结果、截断、失败分开记录，不把“没查到”说成“不存在”。

## 安装

### 安装渠道一览

| 渠道 | 安装方式 | 包含 |
| --- | --- | --- |
| **npx skills**（通用） | `npx skills add luffysolution-svg/sanmu-skills --skill '*' -g` | 三个技能，可单选 |
| **Claude Code** | 插件市场，见下文 | 合集插件 |
| **Codex** | 插件市场，见下文 | 合集插件 |
| **ZCode** | 插件市场或 Release 插件包，见下文 | 合集插件 / 文献检索侧栏可视化插件 |
| **腾讯 SkillHub** | [doc2md](https://skillhub.cn/skills/sanmu-doc2md) · [reader](https://skillhub.cn/skills/sanmu-paper-reader) · [search](https://skillhub.cn/skills/sanmu-paper-search) | 单个技能 |
| **WorkBuddy** | 客户端【专家·技能·连接器 → 技能】中搜索“三木” | 单个技能 |
| **skills.sh / SkillsMP** | [合集页面](https://skills.sh/luffysolution-svg/sanmu-skills)，使用 npx 命令安装 | 三个技能 |
| **GitHub Release** | [下载 ZIP](https://github.com/luffysolution-svg/sanmu-skills/releases/latest) | 合集包与各平台适配包 |

选择一种方式即可，同时用插件和 npx 安装会重复加载。安装技能不会自动装 Python 依赖、KaTeX、OCR 凭证或 Zotero，这些按各技能的说明配置。

### 让 AI 助手安装（推荐）

把下面这段发给你正在用的 AI 编码助手：

```text
请帮我安装并配置三木的 sanmu-skills 技能合集：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git 和 Node.js，把合集安装到当前助手的用户级技能目录：
   npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy
   已有安装时先检查本地修改。
2. sanmu-doc2md：进入它的技能目录（包含 SKILL.md），最好用独立的 Python 虚拟环境：
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
   python scripts/doctor.py
   检查 MINERU_API_TOKEN 是否已配置（不要显示值）；没有就告诉我去 https://mineru.net 申请。
3. sanmu-paper-reader：核心工具只需 Python 3.10+；要用 SI 检索、PDF 附图提取或子图裁切时再装 requirements.txt。
   运行 python scripts/evidence_tools.py --help 确认入口。
4. sanmu-paper-search：核心无第三方依赖，运行 python scripts/paper_search.py doctor。
   只检查 API 凭证是否配置（OPENALEX_API_KEY、SEMANTIC_SCHOLAR_API_KEY、可选 SERPER_API_KEY / EXA_API_KEY），不显示值。
5. 告诉我安装位置、检查结果，以及还需要我配置什么。
```

每个技能的完整安装指令：[doc2md](skills/sanmu-doc2md/README.md#安装) · [reader](skills/sanmu-paper-reader/README.md#安装) · [search](skills/sanmu-paper-search/README.md#安装)

### 命令行

```bash
# 整个合集
npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy

# 只装一个
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g

# 查看与更新
npx skills add luffysolution-svg/sanmu-skills --list
npx skills update
```

需要 Git 和满足 [skills 安装器](https://github.com/vercel-labs/skills) 要求的 Node.js。

### Claude Code

```bash
claude plugin marketplace add luffysolution-svg/sanmu-skills
claude plugin install sanmu-skills@sanmu-skills
```

也可以在会话里一步完成：`/plugin install sanmu-skills --marketplace luffysolution-svg/sanmu-skills`

### Codex

```bash
codex plugin marketplace add luffysolution-svg/sanmu-skills
codex plugin add sanmu-skills@sanmu-skills
```

需要支持 `plugin marketplace` 的新版 Codex CLI。安装后重开会话，可在 `/plugins` 中管理。

### ZCode

```bash
# 安装三合一技能合集
zcode plugin marketplace add luffysolution-svg/sanmu-skills
zcode plugin install sanmu-skills@sanmu-skills
```

若需在 ZCode 桌面端使用带**右侧常驻可视化面板、Obsidian 物理关系图谱与本机 Zotero 双向联动**的独立插件，可从 [GitHub Release](https://github.com/luffysolution-svg/sanmu-skills/releases/latest) 下载 `sanmu-paper-search-zcode-plugin-<版本>.zip` 解压后通过本地插件市场安装。

### 手动安装

```bash
git clone https://github.com/luffysolution-svg/sanmu-skills.git
```

把 `skills/` 下需要的技能目录完整复制或链接到助手的技能目录，例如 `~/.agents/skills/sanmu-doc2md`。

## 使用

装好后直接用自然语言描述任务，助手会自动选用对应技能：

> 把这份扫描教材整理成 Markdown 知识库，按章节组织，保留公式和高清图表，并完成全量校验。

> 精读这篇光催化论文，解释 Fig. 4 和 Fig. 5，区分实验观察和机理推断。

> 围绕 MatterGen 这篇论文分别找参考文献、施引文献和相似研究，看看哪些已经在我的 Zotero 里。

各技能的运行环境、命令和能力边界见各自的使用指南。

## 参与贡献

欢迎通过 [Issues](https://github.com/luffysolution-svg/sanmu-skills/issues) 反馈问题、提交可复现案例或改进建议，也欢迎 Pull Request。

- 每个技能放在 `skills/<技能名>/`，`SKILL.md` 里的 `name` 与目录名一致。
- 修改已有技能时保留实战经验和陷阱说明，只改与问题直接相关的部分。
- 测试在仓库根目录运行：

```bash
python tests/sanmu-doc2md/_selftest.py                       # 需先装 doc2md 的 Python 与 Node 依赖
python -m unittest discover -s tests/sanmu-paper-reader -v
python -m unittest discover -s tests/sanmu-paper-search -v
python -m unittest discover -s distribution/tests -v
node --test tests/zcode-paper-search/test_mcp_server.mjs tests/zcode-paper-search/test_panel.mjs
```

打包、版本与各渠道发布流程见 [渠道发布说明](distribution/README.md)。

## 许可证

[MIT](LICENSE)
