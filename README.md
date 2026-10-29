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

- **多源检索**：OpenAlex、Semantic Scholar、Crossref，可选 SciVerse（结构化文献、全文语义 RAG 与引文关系）、AI4Scholar（S2、Google Scholar、专利、PubMed）、Serper 和 Exa 语义补搜。
- **期刊分区与科研辅助**：easyScholar 分区、IF、CCF 与预警标注；SciVerse Paper Schema 结构化论文剖析与复现材料包；AI4Scholar 科研绘图、矢量化和自动引文。
- **引文扩展与作者消歧**：参考文献、施引文献、相似论文分开记录；同名作者用稳定 ID 与课题方向锚定。
- **全文、原文切片与 SI**：Europe PMC 免 Key 获取 JATS 全文和补充材料原图包并核验撤稿与勘误；SciVerse 按 Unicode 码点偏移读取原文 Markdown 并提取正文高清原图。
- **如实报告**：成功、零结果、截断、失败分开记录，不把“没查到”说成“不存在”。

## 安装

1.10.3 的完整插件包含 **3 个技能、统一 CLI `scripts/sanmu.mjs` 和 24 个 MCP 工具**，在 DeepSeek Harness 中通过原生入口复用完整 ZCode 工作台，在其他终端宿主中提供浏览器工作台。通用运行环境为 **Node.js ≥22.20、Python ≥3.10**；从 GitHub 安装还需 Git。支持 DSH 0.2.1-rc.3 桌面端及其 Electron Node.js 22.20.0。下载版本以 [Release 页面](https://github.com/luffysolution-svg/sanmu-skills/releases/latest)实际提供的资产为准。

### 安装渠道一览

| 渠道 | 安装方式 | 包含 |
| --- | --- | --- |
| **Claude Code** | 一步安装，见下文 | 3 技能 + CLI + MCP + 浏览器工作台 |
| **Codex** | 添加仓库市场后安装，可复制为一行 | 3 技能 + CLI + MCP + 浏览器工作台 |
| **Pi ≥1.4** | `pi install git:github.com/luffysolution-svg/sanmu-skills` | 3 技能 + CLI + 原生 MCP + 浏览器工作台 |
| **DeepSeek Harness** | 现有桌面端【插件 → 添加插件】，输入 GitHub 标识或完整 TGZ 地址 | 3 技能 + CLI + MCP + 完整工作台 |
| **ZCode** | 桌面端插件市场 UI 或 Release 插件 ZIP | 3 技能 + MCP + 常驻侧栏工作台 |
| **npx skills**（通用） | `npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy` | 技能说明与各技能脚本，可单选；不含完整宿主 MCP 安装 |
| **腾讯 SkillHub** | [doc2md](https://skillhub.cn/skills/sanmu-doc2md) · [reader](https://skillhub.cn/skills/sanmu-paper-reader) · [search](https://skillhub.cn/skills/sanmu-paper-search) | 单个技能 |
| **WorkBuddy** | 客户端【专家·技能·连接器 → 技能】中搜索“三木” | 单个技能 |
| **skills.sh / SkillsMP** | [合集页面](https://skills.sh/luffysolution-svg/sanmu-skills)，使用 npx 命令安装 | 技能说明与各技能脚本 |
| **GitHub Release** | 合集 ZIP、ZCode ZIP、各平台技能 ZIP 与完整 `.tgz` | 详见[渠道发布说明](distribution/README.md#构建产物) |

同一宿主选择一种安装方式即可，同时用插件和 npx 安装会重复加载技能。仓库自建 marketplace 不代表 Anthropic、OpenAI 或 ZCode 官方收录；Pi 包目录、npm 与 DSH 的包生态也不代表已通过官方商店审核。

### 让 AI 助手安装（推荐）

把下面这段发给你正在用的 AI 编码助手：

```text
请帮我安装并配置三木的 sanmu-skills：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git、Node.js ≥22.20、Python ≥3.10；支持 DSH 0.2.1-rc.3 的 Electron Node.js 22.20.0。
   已有安装时先检查本地修改，按当前宿主选择一种完整安装方式：
   Claude Code：claude plugin install sanmu-skills --marketplace luffysolution-svg/sanmu-skills
   Codex：codex plugin marketplace add luffysolution-svg/sanmu-skills && codex plugin add sanmu-skills@sanmu-skills
   Pi ≥1.4：pi install git:github.com/luffysolution-svg/sanmu-skills
   DSH 桌面端：在现有「插件 → 添加插件」输入 github:luffysolution-svg/sanmu-skills 或 Release 的完整 TGZ 地址。
   desktop 是保留 profile，由桌面端管理；CLI 仅用于其他已有 profile。安装后从对话头「三木文献检索」或右栏「+」打开完整工作台。
   ZCode：使用桌面端插件市场 UI 添加此 GitHub 仓库，或安装 Release 的 ZCode 插件 ZIP。
2. 定位安装根目录，运行 node "<安装根>/scripts/sanmu.mjs" doctor，只检查状态。
   首次文档解析会自动在数据目录准备私有 Python venv 和 KaTeX；
   如需提前准备，运行 node "<安装根>/scripts/sanmu.mjs" setup。
   不把依赖写入插件安装缓存或系统 Python/Node 环境。
3. 免费检索可先不用 Key。付费 API Key 和 MINERU_API_TOKEN 由我在工作台设置或宿主 userenv 中填写；
   只检查是否配置，不显示值。MinerU Token 可到 https://mineru.net 申请。
   Zotero 写入需 Zotero 8+ 的本机授权，不直接写数据库。
4. 如果当前助手只支持技能目录，则用：
   npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy
   并明确这只安装 SKILL 和各技能脚本，依赖按各技能指南准备，不是完整 MCP/工作台安装。
5. 告诉我安装位置、doctor 结果、如何打开工作台，以及还需要我配置什么。
```

### Claude Code

一步安装：

```bash
claude plugin install sanmu-skills --marketplace luffysolution-svg/sanmu-skills
```

也保留先添加市场再安装的方式：

```bash
claude plugin marketplace add luffysolution-svg/sanmu-skills
claude plugin install sanmu-skills@sanmu-skills
```

会话内也可使用 `/plugin install sanmu-skills --marketplace luffysolution-svg/sanmu-skills`。

### Codex

两步可复制为一行：

```bash
codex plugin marketplace add luffysolution-svg/sanmu-skills && codex plugin add sanmu-skills@sanmu-skills
```

需要支持 `plugin marketplace` 的新版 Codex CLI。安装后重开会话，可在 `/plugins` 中管理。

### Pi

```bash
pi install git:github.com/luffysolution-svg/sanmu-skills
```

需要 **Pi ≥1.4**，通过宿主原生 `registerMcpServer` 接入完整 MCP，无需第三方桥接扩展。

### DeepSeek Harness（DSH）

支持 **DSH 0.2.1-rc.3 桌面端**，兼容其 Electron 内置的 **Node.js 22.20.0**；独立 Node.js 环境需 **≥22.20**，文档解析另需 Python ≥3.10。

#### 桌面端安装

在现有 DSH 桌面端打开 **插件（Plugins）→ 添加插件（Add Plugin）**，输入 GitHub 标识：

```text
github:luffysolution-svg/sanmu-skills
```

也可输入 [Release](https://github.com/luffysolution-svg/sanmu-skills/releases/latest) 中完整 TGZ 的下载地址，例如：

```text
https://github.com/luffysolution-svg/sanmu-skills/releases/download/v1.10.3/sanmu-skills-1.10.3.tgz
```

安装确认后启用 `sanmu-skills`。桌面端保留的 `desktop` profile 通过这个界面管理，普通 `dsh` CLI 不管理该 profile。已有的其他 profile 可使用：

```bash
dsh plugin --profile <已有的非desktop-profile名> add github:luffysolution-svg/sanmu-skills
```

#### 打开与使用

- 点击**对话头部的「三木文献检索」**，或在**右侧栏「+」**中选择同名工作台。
- 工作台直接复用现有 ZCode 完整面板，保留相同的布局、样式和交互逻辑，包括检索、文献详情与内联抽屉、收藏篮、历史批次、API 设置、Zotero、文档解析及现有 **24 个 MCP 工具**的全部操作。
- 引文图谱保留实时力导向布局、节点拖拽与悬停、共被引和桥梁文献分析、年份时间轴。主题与语言环境由 DSH 同步，「交给 AI」发送到当前会话。
- 三个完整技能同时挂载，Agent 可继续使用 `sanmu-paper-search`、`sanmu-doc2md`、`sanmu-paper-reader`。
- 完整工作台随合集一起安装，无需另装侧栏插件。曾安装的 `dsh-plugin-sanmu-sidebar` 应保持禁用，避免重复入口。
- `@deepseek-ai/dsh-mcp-client` 和 `@deepseek-ai/dsh-skill-filesystem` 由宿主提供。跨工作区保存时可显式指定 `saveDir`，或在启动前设置 `SANMU_WORKSPACE_ROOT`。

终端宿主和手动使用仍可运行 `node "<安装根>/scripts/sanmu.mjs" panel` 打开浏览器工作台。使用命令实际返回的完整本地链接建立会话；地址或令牌缺失时重新运行命令获取链接。

### ZCode

在 **ZCode 桌面端插件市场 UI** 添加 `luffysolution-svg/sanmu-skills` 仓库并安装插件；也可从 [GitHub Release](https://github.com/luffysolution-svg/sanmu-skills/releases/latest) 下载 `sanmu-paper-search-zcode-plugin-<版本>.zip`，解压后通过 UI 的本地插件市场入口加载。此单独 ZIP 也包含全部 3 个技能、完整 MCP、关系图谱、Zotero 联动和文档解析工作台。普通桌面用户无需全局 `zcode` 命令，加载时选择自己实际解压的位置。

### 只安装技能 / 手动安装

```bash
# 整个技能合集（SKILL 和各技能脚本）
npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy

# 只装一个
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g

# 查看与更新
npx skills add luffysolution-svg/sanmu-skills --list
npx skills update

# 手动获取源码
git clone https://github.com/luffysolution-svg/sanmu-skills.git
```

npx 需要 Git 和满足 [skills 安装器](https://github.com/vercel-labs/skills)要求的 Node.js；它只安装 SKILL 与各技能脚本。手动安装时，把 `skills/` 下需要的技能目录完整复制或链接到助手的技能目录，例如 `~/.agents/skills/sanmu-doc2md`。这两种方式的 Python 第三方库、KaTeX 和可选功能按各技能指南准备：[doc2md](skills/sanmu-doc2md/README.md#安装) · [reader](skills/sanmu-paper-reader/README.md#安装) · [search](skills/sanmu-paper-search/README.md#安装)。

### 完整包的运行环境与配置

完整包首次文档解析会自动把 Python 第三方依赖装入**数据目录中的私有 venv**，把 KaTeX 准备到 `<数据目录>/doc2md-node/`，不写插件安装缓存或系统环境。可提前运行 `setup`；`doctor` 只读取状态，不安装依赖。

```bash
node "<安装根>/scripts/sanmu.mjs" doctor
node "<安装根>/scripts/sanmu.mjs" setup
```

`SANMU_DATA_DIR` 可显式指定数据目录，`SANMU_PYTHON` 可指定 Python 解释器；`SANMU_NO_AUTO_SETUP=1` 关闭首次使用时的自动依赖准备。API Key 仍需自行填入工作台「设置」或宿主 userenv：免费检索可不填 Key 起步，付费来源需相应账户与额度，MinerU 云端 OCR 需 `MINERU_API_TOKEN`；`local_fast` 可做本地原生提取。Zotero 写入需本机运行 **Zotero 8+** 并允许写授权；可移植 `zotero_library` 工具支持添加笔记和更新条目并回读确认，不直接写数据库。

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
