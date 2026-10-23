<div align="center">

# sanmu-doc2md

**把论文、教材和课件，整理成可维护的 Markdown 知识库。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![KaTeX validation](https://img.shields.io/badge/KaTeX-validation-brightgreen.svg)](https://katex.org/)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-Claude%20%7C%20ZCode%20%7C%20Codex-orange.svg)](https://docs.anthropic.com/en/docs/claude-code/skills)

**简体中文** · [English](README.en.md)

[安装与依赖](#安装与依赖) · [处理能力](#三轨架构分流设计) · [实战案例](#真实实战规模全景) · [命令速查](#常用命令速查)

</div>

把扫描教材、PDF 图书、理工科课件、中英文学术论文、硕博学位论文等文档整理成带高清图片、表格和可渲染 LaTeX 公式的 Markdown 知识库，供支持 [Agent Skills](https://agentskills.io) 的 AI 助手调用。

不只是文本提取。它专注解决理工科学术文档的真正痛点：**公式全量 KaTeX 编译、图表回源保真、多面板错切修复、表格内二维码保留、OCR 错字校对、超长学位论文与教材分片组装、孤儿切图安全清理，以及期刊 PDF 与 Zotero 元数据头部**。编译通过不等于内容完整，图表与科学符号仍须逐项核对原文，并经真实客户端渲染终验。

---

## 安装与依赖

### 方式一：让 AI 助手安装配置（推荐）

将下面的指令复制给 Claude Code、Codex、ZCode、Cursor 等 AI 编码助手，让它完成技能安装、依赖配置和环境校验：

```text
请帮我安装并配置 sanmu-doc2md 技能，仓库地址：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git、Node.js 和 Python 环境，通过下面的命令将技能安装到当前 AI 助手的用户级技能目录：
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
   根据当前助手选择对应安装目标；已有安装时先检查本地修改，避免覆盖。
2. 找到安装器输出的 sanmu-doc2md 目录，确认其中有 SKILL.md、requirements.txt 和 scripts/。
   后续命令均从该技能目录执行；不要把合集仓库根目录当作技能目录。
3. 优先使用独立 Python 虚拟环境，安装 Python 依赖：
   python -m pip install -r requirements.txt
4. 安装公式校验所需的 Node.js 依赖：
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
5. 使用配置好的 Python 做环境与最小运行检查（不提交 OCR）：
   python scripts/doctor.py
   python scripts/build_markdown.py --help
6. 检查 MINERU_API_TOKEN 是否配置；不要显示凭证值。
   如未配置，提示我从 https://mineru.net 获取并在本地设置。
   已配置时运行 python scripts/doctor.py --require-ocr，检查配置状态。
7. 告诉我技能安装位置、验证结果和仍需配置的项目。
```

### 方式二：命令行安装

```bash
# 安装单个技能（全局）
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g

# 安装整个合集（全局）
npx skills add luffysolution-svg/sanmu-skills --skill '*' -g
```

进入安装器输出的 `sanmu-doc2md` 技能目录（包含 `SKILL.md`）；从源码运行时先进入 `skills/sanmu-doc2md`。下列依赖和常用命令均相对该目录执行。

```bash
python -m pip install -r requirements.txt
npm --prefix scripts install --ignore-scripts --no-audit --no-fund
python scripts/doctor.py
python scripts/build_markdown.py --help
```

可先创建 Python 虚拟环境再安装依赖。`doctor.py` 检查环境与凭证配置，并试编译一个基础公式；不安装依赖、不输出凭证值；`--json` 输出结构化结果。`build_markdown.py --help` 检查装配入口能否启动，不代表完成文档转换。开发测试见仓库的[参与贡献](https://github.com/luffysolution-svg/sanmu-skills#参与贡献)。

云端 OCR 需要设置 `MINERU_API_TOKEN`（获取方式见 [mineru.net](https://mineru.net)）。使用 `python scripts/doctor.py --require-ocr` 将缺少凭证视为检查失败；未配置凭证不影响满足依赖的本地装配与校验。

---

## 解决什么问题

用普通 OCR 工具直接转换理工科教材或学术论文，通常会遇到：

- **公式结构崩解**：散落为孤立字符，上下标、分式和希腊字母错乱；
- **排版倒置混乱**：课件逻辑盲目把整页图片置顶，长教材阅读顺序颠倒，页顶首句被误提为二级标题；
- **客户端渲染劫持**：正文中方括号注释（如 `[通常为 $\mathcal{N}(0, I)$ ]`）被 Obsidian 等编辑器误判为超链接，导致公式源码裸露为蓝色；
- **标题公式破坏性降级**：标题中的分子式（如 `$\mathrm{CO}_2$`）被粗暴剔除定界符，留下裸下划线 `CO_2` 伪代码并引发 Markdown 斜体冲突；
- **资产碎片堆积**：微调裁切框后，旧切图文件残留堆积，目录体积虚高；
- **关键资源丢失**：教材章节末尾的拓展学习二维码、复杂数据表被纯文本转换吞掉；
- **同音字/形近字错漏**："轫致辐射"→"韧致辐射"、"ν"→"v"、"N₀"→"No"；
- **模型推理泄漏**：多模态 OCR 模型的 "The Ground Truth image displays..." 等提示词进入正文。

本 Skill 提供全量公式语法预检与真实客户端渲染核验。Obsidian 使用 MathJax，本地 KaTeX 检查仅作预检；引擎支持差异须结合原页和客户端确认。规则来自教材与学术文献的处理案例。

---

## 三轨架构分流设计

针对不同文档形态，流水线采用三轨专业化分流架构，严禁单一模板生搬硬套：

| 维度 | 1. 扫描教材与长篇专著 (`--mode book`) | 2. 课件幻灯片与讲义 (`--mode slides`) | 3. 学术科研文献与学位论文 |
| :--- | :--- | :--- | :--- |
| **组织架构** | 多章节分流（`01-第一章.md`、`00-目录.md`） | 按讲次/课时组织 | 单篇独立文献一体化（主笔记 + `00-目录.md`） |
| **排版流向** | **几何自然流**混排（$\Delta y = 25$），严禁插图置顶 | **视觉置顶**排版（图置顶，承接文字与要点） | 默认保留经核对的物理 OCR 双栏阅读流 |
| **标题与习题** | 严格章节判定，精准区分小节标题与习题题干 | 保持幻灯片页面核心主题 | 统一学术标准命名 `{{firstCreator}} - {{year}} - {{title}}.md` |
| **图表处理** | 原 PDF 至少 3× 高清重裁，多面板区域合并 | 优先提取 PPTX 内嵌原图，清洗图内小标签 | 高置信度多面板缝合，支持 SI 补充材料 |
| **元数据** | 保留实质性学术脚注，清洗运行页眉页脚 | 对齐原生文本层，按相似度校正错字 | 提取完整 YAML 元数据（DOI、作者、摘要、Zotero URI） |

---

## 包含什么

```text
SKILL.md                        技能定义、执行流程与边界
references/
  mineru-api.md                 MinerU API 契约、归一化坐标与 DoH 连接策略
  math-normalization.md         科学公式归一化规则，附真实前后对照
  book-workflow.md              超长教材与学术专著分片识别、重组指引
  research-documents.md         学术论文与学位论文标准命名、元数据与双栏规范
  zotero-sync.md                只读 API 与 MCP 同步、回读验证
  repairs.md                    回源修复：文字、图块、题注与扫描噪声
scripts/
  doctor.py                     环境与凭证配置检查、基础公式编译探测
  mineru_ocr.py                 大文档切分 (split) / 批量提交 / 轮询 / 容错下载
  build_markdown.py             Markdown 组装核心（支持 slides / book / 孤儿切图清理）
  build_research_document.py    科研文献专用一体化装配入口
  normalize_math.py             LaTeX 公式归一化引擎（命令分离、方括号防劫持、Unicode退避）
  extract_formulas.py           从 Markdown 抽取全部公式
  validate_output.py            交付前结构校验、宏命令残留与模型提示词泄漏检测
  validate_math.cjs             KaTeX 全量公式语法预检
  preview_server.py             本地离线 Markdown + KaTeX 预览沙箱
```

实战记录见下方[案例文档](#真实实战规模全景)；[可复现示例](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/book-demo/demo.md)保存在仓库中，不随运行技能安装。

---

## 核心流程

```text
[1. 源文预检] ── 原生文字层与内嵌原图排查，形态分流（教材 / 课件 / 科研文献）
        │
[2. 切片与 OCR]    ── 超长 PDF 自动切块（96页/块）批量提交 MinerU 云端 API
        │
[3. 文字交叉校对]  ── 利用原生文字层/PPT文本对齐校对 OCR 形近字与拼写
        │
[4. 图表回源保真]  ── 提取内嵌原图 / PDF 3× 高清重裁，多面板外接矩形合并，保留表内二维码
        │
[5. 公式规范归一]  ── 公式规则标准化（中文进 \text、不等号转义、方括号防劫持、标题公式保全）
        │
[6. 知识库组装]    ── 自然混排或视觉置顶，生成 00-目录.md 导航，可选 --prune-orphans 清理孤儿切图
        │
[7. 语法与显示检查] ── 全量 KaTeX 语法预检 + Obsidian MathJax 实际视图核验
```

---

## 真实实战规模全景

仓库案例文档记录了以下处理结果；这些数字描述相应案例，不代表对任意文档的质量保证：

| 案例库 | 文档类型与规模 | 提取公式数 | 图片/图表数 | 质量指标与技术突破 |
| :--- | :--- | :---: | :---: | :--- |
| **案例一：材料分析测试方法** | 10 章 694 页 PPT 课件 | 2,118 个 | 199 张 | 公式 100% 通过，原生文字纠错 7 处，图片去重 |
| **案例二：物理化学（第六版）下册** | 556 页全扫描经典高校教材 | **7,523 个** | 332 张 | **KaTeX 7,523/7,523 全部通过（0 失败）**，提取 28 个拓展二维码 |
| **案例三：中文科研文献与硕博学位论文全集** | 5 篇共 611 页（含 240 页博士论文） | **5,797 个** | 398 张 | 解决 Obsidian 方括号链接劫持、保全标题化学式、**安全清理 89 个历史旧图** |
| **案例四：英文前沿期刊文献集** | 7 篇共 87 页顶级英文期刊 (Nature/Matter 等) | 876 个 | 114 张 | 双栏阅读流还原，突破 Windows 路径长度瓶颈，YAML 元数据 全量注入 |
| **总计** | **近 2,000 页真实学术文档** | **16,314 个** | **1,043 张** | **案例记录的公式全部通过编译** |

*详见 [examples/RESULTS.md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESULTS.md) 与 [examples/RESEARCH-VALIDATION.md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESEARCH-VALIDATION.md)。*

---



## 常用命令速查

```bash
# 1. 超长文档切片（以 96 页切片为例）
python scripts/mineru_ocr.py split path/to/document.pdf --out-root ./ocr-work --chunk-size 96

# 2. 批量提交 MinerU 云端 API
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv

# 3. 教材模式组装（自然流混排、表内二维码提取、双轨扫描图备查、清理旧图）
python scripts/build_markdown.py \
    --raw ./ocr-work/raw \
    --out ./知识库 \
    --mode book \
    --manifest ./ocr-work/manifest.json \
    --chapters ./chapters.json \
    --pdf path/to/document.pdf \
    --keep-table-images \
    --prune-orphans

# 4. 课件模式组装（PPTX 原生文字校对与原图提取）
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode slides --source-dir ./课件 --pdf-dir ./课件PDF

# 5. 科研文献一体化组装（Zotero 学术标准命名、元数据注入、双栏流）
python scripts/build_research_document.py --pdf ./source.pdf --raw ./ocr-work/raw/article \
    --metadata ./metadata.json --out "./科研知识库/第一作者 等 - 年份 - 论文标题"

# 6. 交付前结构与链接完整性校验
python scripts/validate_output.py ./知识库 --images-dir images

# 7. 全量 KaTeX 公式语法预检，失败项回源处理
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json

# 8. 本地离线预览验证沙箱
python scripts/preview_server.py ./知识库 --port 8766 --katex-dir scripts/node_modules/katex/dist
```

---

## 输出规范

产出的知识库完全为人类长效阅读与智能体（RAG/Agent）索引设计：
- **干净交付**：默认仅导航、正文 Markdown 与图片；构建报告和增量状态存放在外部 `.<目录名>.build/`。
- **全相对路径**：图片采用 `![](images/...)` 标准相对格式，知识库目录整体可自由移动或跨设备同步；
- **双模态保真**：公式输出标准 LaTeX，复杂科学表格与复杂化学环状键保留原扫描图裁片双轨备查；
- **全角方括号规范**：解释性注释发生链接解析冲突时，核对后可采用 `［...］`，保留真实引用、链接和数学分组；
- **入口导航**：根目录附带标准 URI 编码的 `00-目录.md`，在目标 Obsidian 库中核验跳转。
- **来源与人工内容**：保留源文件、页码映射和修复依据；默认保护人工修改，不用 `--overwrite` 绕过差异。`page_footnote` 的学术注释、来源和公式说明须保留，不能按标签一律丢弃。

---

## 许可证

[MIT](LICENSE)
