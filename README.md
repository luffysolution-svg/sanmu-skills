<div align="center">

# sanmu-skills

**三木的 Agent Skills 合集**

把实际使用中沉淀的工作方法，整理成可安装、可复用的技能。

[![自动化测试](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT 许可证](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md)

[快速安装](#快速安装) · [市场安装](#市场安装) · [技能目录](#技能目录) · [使用说明](#使用说明) · [参与贡献](#参与贡献)

</div>

## 快速安装

### 让 AI 助手安装（推荐）

复制下面的指令，发给你正在使用的 AI 编码助手：

```text
请帮我安装并配置三木的 sanmu-skills 技能合集：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git 和 Node.js，通过以下命令安装合集到当前 AI 助手的用户级技能目录：
   npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy
   选择当前助手对应的安装目标；已有安装时先检查本地修改。
2. 阅读已安装技能的 README，按各自要求配置依赖。
3. 对 sanmu-doc2md，进入包含 SKILL.md 的技能目录，优先使用独立 Python 虚拟环境，再执行：
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
   python scripts/doctor.py
   python scripts/build_markdown.py --help
4. 检查 MINERU_API_TOKEN 配置状态，不显示凭证值；未配置时指导我从 https://mineru.net 获取并在本地设置。
5. 对 sanmu-paper-reader，证据、主题与基准工具使用 Python 3.10+ 标准库；需要 SI 探针、PDF 附图提取或子图裁切时，在独立虚拟环境执行 python -m pip install -r requirements.txt。从技能目录运行 python scripts/evidence_tools.py --help 和 python scripts/benchmark_collector.py --help；PDF/OCR/Zotero 按实际输入检查。
6. 对 sanmu-paper-search，使用 Python 3.10+ 从技能目录运行 python scripts/paper_search.py doctor 和 python scripts/paper_search.py --help；核心无第三方依赖，学术 API 凭证与可选 SERPER_API_KEY 仅检查是否配置，Zotero 按需连接；Serper 支持 Google Scholar、网页、专利等公开检索。
7. 汇报安装位置、验证结果和仍需配置的项目。
```

完整 AI 安装指令：[文档解析](skills/sanmu-doc2md/README.md#方式一让-ai-助手安装配置推荐) · [文献解读](skills/sanmu-paper-reader/README.md#让ai助手安装推荐) · [文献检索](skills/sanmu-paper-search/README.md#让-ai-助手安装推荐)

### 命令行安装

安装整个合集到用户目录：

```bash
npx skills add luffysolution-svg/sanmu-skills --skill '*' -g --copy
```

安装器会引导你选择目标 AI 助手和安装方式。需要 Git 和满足 [skills 安装器要求](https://github.com/vercel-labs/skills) 的 Node.js。

只安装指定技能：

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
```

## 市场安装

### Claude Code marketplace

在终端添加本仓库的市场并安装合集插件：

```bash
claude plugin marketplace add luffysolution-svg/sanmu-skills
claude plugin install sanmu-skills@sanmu-skills
```

Claude Code 2.1.275+ 也可在会话中一步安装：

```text
/plugin install sanmu-skills --marketplace luffysolution-svg/sanmu-skills
```

### Codex marketplace

```bash
codex plugin marketplace add luffysolution-svg/sanmu-skills
codex plugin add sanmu-skills@sanmu-skills
```

需要支持 `plugin marketplace` 的新版 Codex CLI；安装后重开会话，也可在 `/plugins` 中管理。Claude 与 Codex 的合集插件均包含三个技能。选择插件或 `npx skills` 中的一种安装方式，避免重复加载。

### 腾讯 SkillHub、SkillsMP 与 skills.sh

- **腾讯 SkillHub**：三个技能已发布：[sanmu-doc2md](https://skillhub.cn/skills/sanmu-doc2md)、[sanmu-paper-reader](https://skillhub.cn/skills/sanmu-paper-reader)、[sanmu-paper-search](https://skillhub.cn/skills/sanmu-paper-search)。也可从 [GitHub Release](https://github.com/luffysolution-svg/sanmu-skills/releases) 下载独立适配包。
- **SkillsMP**：从公开 GitHub 自动索引，实际展示以其同步结果为准；安装使用本页命令。
- **skills.sh**：[合集页面](https://skills.sh/luffysolution-svg/sanmu-skills) 展示三个技能，使用上方 `npx skills add` 安装。

技能或插件安装不会自动安装 Python、KaTeX、OCR 服务或 Zotero。运行依赖与凭证按各技能指南配置。维护者的打包、版本和发布步骤见 [渠道发布说明](https://github.com/luffysolution-svg/sanmu-skills/blob/main/distribution/README.md)。

## 技能目录

| 技能 | 用途 | 文档 |
| --- | --- | --- |
| **sanmu-doc2md** | 将论文、教材和课件整理成保留 LaTeX 公式、高清图表与 Zotero 元数据的 Markdown 知识库。 | [使用指南](skills/sanmu-doc2md/README.md) |
| **sanmu-paper-reader** | 单篇精读、跨文献对比、跨学科与跨主题解读及知识库问答，优先材料与化学，保留可追溯证据。 | [使用指南](skills/sanmu-paper-reader/README.md) |
| **sanmu-paper-search** | 从课题、种子或作者检索文献，核对题录、扩展引文、定位全文/SI，并匹配 Zotero 收藏。 | [使用指南](skills/sanmu-paper-search/README.md) |

### sanmu-doc2md

面向公式多、图表多、版面复杂的学术文档，覆盖从识别、校对到知识库装配与校验的完整流程。

- **按文档组织**：教材分章、课件按讲次整理、论文独立成篇。
- **保留科学内容**：公式全量编译校验，图表回源提取与高清裁切。
- **连接文献管理**：支持 Zotero 元数据、批注同步与主文/补充材料双链。
- **便于长期维护**：相对路径、独立图片目录、外置构建状态与受保护的增量更新。

[查看完整说明与案例 →](skills/sanmu-doc2md/README.md)

### sanmu-paper-reader

面向已有论文、SI、Zotero 附件与解析后的 Markdown，支持深入精读与知识积累，重点对齐证据边界与实验条件。

- **按场景分层**：支持单篇精读、跨文献对比、跨学科迁移、跨主题综合与知识库问答五大场景。
- **图文对照与子图切片**：提供 Obsidian 悬停气泡预览、折叠抽屉 Callout 与单面板精准切片，阅读文字即刻核验谱图。
- **实验复现 SOP 与配方卡**：提取毫克级投料、溶剂体积、升温退火曲线、反应液配比与已知复现缺口。
- **机制图与 SI 定位**：按需绘制 Mermaid 机制图，对有文字层的 SI 检索关键词、定位题注并导出候选图像，结合原页核对完整性。
- **性能基准与学术标签**：核对指标、单位和实验工况后写入基准表，保留同一论文的不同条件记录；按需更新 Zotero 标签。

[查看使用指南与完整效果展示 →](skills/sanmu-paper-reader/README.md)

### sanmu-paper-search

从研究问题、种子论文或作者出发，查询 OpenAlex、Semantic Scholar 与 Crossref，保留来源、去重依据、失败和截断状态；定位全文/SI并匹配已有 Zotero 材料。可选配置 Serper，补充 Google Scholar、网页、专利等 13 类公开接口。日常检索保持轻量，扩展任务记录协议，取得材料后衔接转换与精读。

[查看安装、命令与能力边界 →](skills/sanmu-paper-search/README.md)

## 使用说明

安装后，在支持 Agent Skills 的 AI 助手中描述任务即可，例如：

> 把这份扫描教材整理成 Markdown 知识库，按章节组织，保留公式和高清图表，并完成全量校验。

每个技能的运行环境和输入要求以其使用指南为准。`sanmu-doc2md` 需要 Python 和 KaTeX；云端 OCR 另需配置 MinerU 凭证。先按 [安装与依赖](skills/sanmu-doc2md/README.md#安装与依赖) 配置，再运行 `scripts/doctor.py` 检查环境。

### 查看与更新

```bash
# 查看合集中的技能
npx skills add luffysolution-svg/sanmu-skills --list

# 更新已安装的技能
npx skills update
```

### 手动安装

```bash
git clone https://github.com/luffysolution-svg/sanmu-skills.git
```

将 `skills/` 下需要的技能目录完整复制或链接到 AI 助手的技能目录，例如 `~/.agents/skills/sanmu-doc2md`。目录内包含运行脚本、参考文档和安装说明；演示案例位于仓库的 `examples/`，开发测试位于 `tests/`。

## 参与贡献

欢迎通过 [问题反馈](https://github.com/luffysolution-svg/sanmu-skills/issues) 提交使用问题、可复现案例和改进建议，也可以提交拉取请求。

每个技能放在 `skills/<技能名>/` 下，`SKILL.md` 中的名称与目录名保持一致。修改已有技能时保留实战经验，只调整与问题直接相关的内容。

验证 `sanmu-doc2md`：

```bash
python -m pip install -r skills/sanmu-doc2md/requirements.txt
npm --prefix skills/sanmu-doc2md/scripts install --ignore-scripts --no-audit --no-fund
python -m unittest discover -s tests/sanmu-doc2md -v
python skills/sanmu-doc2md/scripts/doctor.py
```

验证 `sanmu-paper-reader`（Python 3.10+；SI/PDF/图像工具的可选依赖见其安装说明）：

```bash
python -m unittest discover -s tests/sanmu-paper-reader -v
```

验证 `sanmu-paper-search`（Python 3.10+，核心无第三方依赖）：

```bash
python -m unittest discover -s tests/sanmu-paper-search -v
python skills/sanmu-paper-search/scripts/paper_search.py doctor
```

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
