<div align="center">

# sanmu-skills

**三木的科研 Agent Skills 合集**

文档转换 · 文献精读 · 文献检索：把实际科研中沉淀的工作方法，整理成可安装、可复用的技能。

[![Release](https://img.shields.io/github/v/release/luffysolution-svg/sanmu-skills?label=release)](https://github.com/luffysolution-svg/sanmu-skills/releases/latest)
[![自动化测试](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-standard-orange.svg)](https://agentskills.io)
[![MIT 许可证](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md)

[技能一览](#技能一览) · [效果图览](#效果图览) · [安装](#安装) · [使用](#使用) · [参与贡献](#参与贡献)

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

## 效果图览

> 点击下方分类卡片展开查看高清实机截图与交互演示（共 32 张全景展示，涵盖图谱、检索、解析、精读及各宿主实机界面）：

<details>
<summary><b>🌌 关系图谱全景与时间轴分析</b> <i>— 查看星系图谱全景、跨度20年引文网络与桥梁文献标记</i></summary>

#### 1. 物理力导向星系图谱动态交互演示
> 基于 Obsidian 物理力场引擎构建，支持鼠标拖拽碰撞、弹性回弹、节点悬停实时预览文献题录元数据。

![力导向星系图谱交互演示](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-57-59.gif)

#### 2. 年份演进时间轴动态交互演示
> 按发表年份横向展开文献脉络，纵向标定被引权重与层级，鼠标滑动实时高亮施引与参考文献流向。

![年份演进时间轴交互演示](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-59-03.gif)

#### 3. 力导向星系静态全景与核心情报
> 中心种子文献辐射展开，自动发现识别「共被引基石文献」与「桥梁文献」，提供图谱核心情报统计。

![引文与共著者关系图谱全景](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-46-24.png)

#### 4. 年份演进时间轴全景视图
> 清晰呈现 2004 至 2023 年跨度 20 年的引文脉络演进，直观把控课题技术发展历史。

![年份演进时间轴图谱](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-46-46.png)

![引文网络全景与桥梁文献标记](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-59-57.png)

</details>

<details>
<summary><b>🔍 多源学术文献检索与原位引文追溯（sanmu-paper-search）</b> <i>— 点击展开查看检索通道、原位抽屉与批次分析</i></summary>

#### 1. 14+ 项拓展检索通道
> 支持 SciVerse 全文语义 RAG、谷歌学术、全球专利、PubMed、Semantic Scholar 全文片段及 Exa AI 语义检索。

![多源拓展检索通道](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-47-36.png)

#### 2. 多源题录精确核验
> 跨 OpenAlex、Semantic Scholar、Crossref 实时校验真实题录，自动探测开放获取 (OA) 全文与可下载 PDF。

![多源精确核验](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-49-17.png)

#### 3. 检索结果卡片与中科院分区标注
> 集成 easyScholar 权威等级（中科院 1/2 区 TOP、JCR Q1、IF、预警标注）、AI 智能要点 (TLDR)、随手批注与课题标签。

![检索结果与分区标注](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-47-17.png)

#### 4. 原位展开施引文献（最新追踪抽屉）
> 点击「施引」直接在当前卡片内联展开最新施引工作，不跳转页面、不丢失当前检索上下文，并自动同步至关系图谱。

![原位展开施引文献](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-49-37.png)

#### 5. 原位展开参考文献（向前溯源抽屉）
> 点击「参考文献」原位向前溯源基石文献，支持一键跨源补齐摘要、直接加入收藏篮或存入 Zotero。

![原位展开参考文献](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-53-45.png)

#### 6. 搜索历史记录与批次进阶分析
> 完整记录每次检索与追溯批次，支持勾选多条历史记录执行「去重合并 (Merge)」或「前后对比 (Diff)」。

![历史批次与合并对比](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-49-50.png)

</details>

<details>
<summary><b>📝 学术文档解析为 Obsidian 知识库（sanmu-doc2md）</b> <i>— 点击展开查看长篇教材切片、公式还原与顶刊入库</i></summary>

#### 1. 从本机 Zotero 文库直接点选文档
> 自动读取本地 Zotero 分类目录与条目，带入完整题录元数据与本地 PDF/SI 附件，一键发起高精度解析。

![Zotero分类文档挑选](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-50-34.png)

#### 2. 多种学术装配模式
> 支持科研论文双链装配、课件 slides 讲义重构、长篇教材 book 智能切片与章节重组。

![解析装配模式](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-50-50.png)

#### 3. 教材切片解析实机展示：《材料科学基础》
> 自动按章节拆分为独立笔记，提取晶体缺陷等高清矢量插图，形成整洁的 Obsidian 树状知识库。

![教材解析-材料科学基础](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-10-14.png)

#### 4. 高难数学物理公式 100% 渲染：《物理化学》统计热力学
> 连乘、连加、微观状态数等复杂公式完美转为标准 LaTeX，全量通过 KaTeX 校验，无乱码伪影。

![教材解析-物理化学公式](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-11-31.png)

#### 5. 工科高清工程图纸保留：《化工制图》
> 完美保留机械制图正投影等复杂几何图纸与全套标准数据表，支持 3 倍高清原图裁切。

![教材解析-化工制图工程图](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-12-05.png)

#### 6. 自动生成的知识库目录导航
> 自动生成包含书目信息、章节索引、插图统计与公式编译通过率的完整知识库导航页。

![教材解析-知识库导航索引](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-12-38.png)

#### 7. 顶刊论文解析入库效果：《Nature Machine Intelligence》
> 自动生成 Obsidian Properties 结构化题录、双向链接 SI 补充材料笔记，正文排版工整优雅。

![顶刊论文解析入库](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-15-09.png)

</details>

<details>
<summary><b>📖 证据链文献精读与深度报告（sanmu-paper-reader）</b> <i>— 点击展开查看精读长文、机理图表与页码溯源</i></summary>

#### 1. 对标同行评审标准的深度精读长文报告
> 自动补齐核心题录、催化机理剖析、光催化析氢速率严格 Benchmark 对比及对当前课题的直接启发。

![文献深度精读报告](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-02-06.png)

#### 2. Obsidian 结构化精读长笔记与方法链拆解
> 包含微调 LLM 概念抽取、时序图神经网络架构、Mermaid 方法链流程图与严格的实验验证假设。

![Obsidian文献精读长笔记](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-16-47.png)

#### 3. 图文折叠对照与物理页码考证
> 自动插入折叠 Callout 原图对照，每一个关键结论均核验对应的正文页码或无印刷页码物理页。

![精读图文对照与页码考证](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-16-59.png)

</details>

<details>
<summary><b>🎨 科研绘图、矢量化与自动引文（AI4Scholar 科研工具箱）</b> <i>— 点击展开查看科研绘图与顶刊风格预设</i></summary>

#### 1. AI4Scholar 科研模式矩阵
> 支持智能科研绘图、文生图、风格迁移、指令改图、位图转矢量 PDF+可编辑 PPTX、纯矢量 SVG、专家审图与学术段落自动引文。

![科研绘图模式矩阵](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-47-51.png)

#### 2. 学术专业风格预设与参数微调
> 支持 16:9 / 1:1 等画幅与 2K 高清，内置 Nature 顶刊风、BioRender 生物医学风、催化机理示意图、3D 纳米材料渲染等预设。

![绘图风格与参数配置](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-48-12.png)

</details>

<details>
<summary><b>🖥️ 多宿主原生接入与配置工作台（DeepSeek Harness / ZCode / Zotero）</b> <i>— 点击展开查看客户端实机安装与设置中心</i></summary>

#### 1. DeepSeek Harness 桌面端：添加插件
> 在 DSH 桌面端直接输入 `github:luffysolution-svg/sanmu-skills` 即可一键拉取安装。

![DSH桌面端添加插件](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-03-09.png)

#### 2. DeepSeek Harness 桌面端：插件启用
> 安装后在插件列表中开启「三木文献检索」，随合集自动装配 3 个技能与 24 个 MCP 工具。

![DSH插件启用状态](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-03-50.png)

#### 3. DeepSeek Harness 桌面端：常驻侧栏入口
> 右侧栏一键调出 1:1 还原的完整科研工作台，无需配置额外端口。

![DSH右侧栏工作台入口](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-02-34.png)

#### 4. ZCode 桌面端：添加插件市场
> 在 ZCode 插件市场点击「添加」填入仓库标识快速接入。

![ZCode添加插件市场](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-05-58.png)

#### 5. ZCode 桌面端：个人插件管理
> 市场中即点即用，支持版本无缝更新与状态查看。

![ZCode个人插件列表](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-06-23.png)

#### 6. ZCode 桌面端：原生工作台与对话协同
> 左侧 AI 智能对话，右侧常驻三木工作台，图谱、检索、Zotero 状态实时互通。

![ZCode右侧栏工作台](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-07-16.png)

#### 7. Zotero 本地 API 授权
> 严格遵循安全规范，调用本地接口时弹出官方授权提示，保障文献数据安全。

![Zotero本地API授权](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-54-10.png)

#### 8. 同步存入 Zotero 文库与标签
> 一键将题录、分区标签及随手批注同步写回 Zotero 对应分类。

![同步存入Zotero文库](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_12-54-59.png)

#### 9. 全源 API 凭据、Zotero 与存储统一设置中心
> 9 种学术与 AI 数据源 Key、Zotero 服务地址及下载目录一站式可视化配置与自检。

![API与工作台设置中心](https://obsidian-image-bed-1379524953.cos.ap-guangzhou.myqcloud.com/Obsidian/PixPin_2026-10-29_13-08-21.png)

</details>

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

安装确认后启用 `sanmu-skills`，即可直接在对话头部或右侧栏打开完整工作台。桌面端保留的 `desktop` profile 通过这个界面管理，普通 `dsh` CLI 不管理该 profile。已有的其他 profile 可使用：

```bash
dsh plugin --profile <已有的非desktop-profile名> add github:luffysolution-svg/sanmu-skills
```

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
