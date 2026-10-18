<div align="center">

# sanmu-skills

**三木的 Agent Skills 合集**

把实际使用中沉淀的工作方法，整理成可安装、可复用的技能。

[![自动化测试](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT 许可证](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md)

[快速安装](#快速安装) · [技能目录](#技能目录) · [使用说明](#使用说明) · [参与贡献](#参与贡献)

</div>

## 快速安装

### 让 AI 助手安装（推荐）

复制下面的指令，发给你正在使用的 AI 编码助手：

```text
请帮我安装并配置三木的 sanmu-skills 技能合集：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git 和 Node.js，通过以下命令安装合集到当前 AI 助手的用户级技能目录：
   npx skills add luffysolution-svg/sanmu-skills --skill '*' -g
   选择当前助手对应的安装目标；已有安装时先检查本地修改。
2. 阅读已安装技能的 README，按各自要求配置依赖。
3. 对 sanmu-doc2md，进入包含 SKILL.md 的技能目录，优先使用独立 Python 虚拟环境，再执行：
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
   python scripts/doctor.py
   python -m unittest discover -s tests -v
4. 检查 MINERU_API_TOKEN 配置状态，不显示凭证值；未配置时指导我从 https://mineru.net 获取并在本地设置。
5. 对 sanmu-paper-reader，使用 Python 3.10+ 从技能目录执行 python -m unittest discover -s tests -v；附带证据工具无第三方依赖，PDF/OCR/Zotero按实际输入检查可用性。
6. 对 sanmu-paper-search，使用 Python 3.10+ 从技能目录运行 python scripts/paper_search.py doctor 和 python -m unittest discover -s tests -v；核心为标准库，学术 API 凭证仅检查是否配置。Zotero 按需运行 python scripts/zotero_local.py doctor，无需宿主 MCP 或云端 API key；首次写入在已授权范围内用 doctor --apply 触发 Zotero 本地授权。全文后端按需在用户缓存准备 ScanSci：优先复用审计匹配的已有版本，否则固定 1.18.2（新建环境需 Python 3.11+），不要求手装或配置 Tor；机构访问交助手浏览器工具复用已授权会话。附件同步由本机 Zotero 管理，未核对时不宣称 WebDAV 完成。
7. 汇报安装位置、验证结果和仍需配置的项目。
```

完整 AI 安装指令：[文档解析](skills/sanmu-doc2md/README.md#方式一让-ai-助手安装配置推荐) · [文献解读](skills/sanmu-paper-reader/README.md#让ai助手安装推荐) · [文献检索](skills/sanmu-paper-search/README.md#让-ai-助手安装推荐)

### 命令行安装

安装整个合集到用户目录：

```bash
npx skills add luffysolution-svg/sanmu-skills --skill '*' -g
```

安装器会引导你选择目标 AI 助手和安装方式。需要 Git 和满足 [skills 安装器要求](https://github.com/vercel-labs/skills) 的 Node.js。

只安装指定技能：

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
```

## 技能目录

| 技能 | 用途 | 文档 |
| --- | --- | --- |
| **sanmu-doc2md** | 将论文、教材和课件整理成保留 LaTeX 公式、高清图表与 Zotero 元数据的 Markdown 知识库。 | [使用指南](skills/sanmu-doc2md/README.md) |
| **sanmu-paper-reader** | 单篇精读、跨文献对比、跨学科与跨主题解读及知识库问答，优先材料与化学，保留可追溯证据。 | [使用指南](skills/sanmu-paper-reader/README.md) |
| **sanmu-paper-search** | 从课题、种子或作者检索文献，核对题录、扩展引文、获取可用主文/SI，并通过独立本地客户端查重写入 Zotero。 | [使用指南](skills/sanmu-paper-search/README.md) |

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
- **原生 Mermaid 机制流与 SI 探针**：零报错渲染能带电荷流与 FLPs 水分子极化；毫秒级穿透 50+ 页 SI 并全量提取高清附图。
- **基准入库与学术标签**：自动提取核心催化指标追加录入 `benchmarks.csv`，并同步反哺 Zotero 学术标签。

[查看使用指南与完整效果展示 →](skills/sanmu-paper-reader/README.md)

### sanmu-paper-search

从研究问题、种子论文或作者出发，默认查询 OpenAlex、Semantic Scholar 与 Crossref；arXiv 和 Europe PMC 用 `--sources arxiv,europe-pmc` 显式选择，不增加默认请求。保留来源、去重依据、失败和截断状态，区分免费可读与开放许可，预印本与正式 DOI 保留关系。日常检索保持轻量，扩展任务记录协议。

Python 3.10+ 标准库核心独立运行；`zotero_local.py` 无需宿主 MCP 或云端 API key，首次写入由 Zotero 本地对话框授权。`acquire.py` 用独立 manifest 分别记录主文和 SI，出版社 HTML 候选需显式选择；`ingest.py` 校验哈希与身份后查重写入。附件导入后的 WebDAV 同步由本机 Zotero 完成，需另行确认状态。

可选 ScanSci 在用户缓存按需自动准备，优先复用经函数审计匹配的已有版本（含旧版），否则固定 `scansci-pdf==1.18.2`，新建环境需 Python 3.11+。仅运行经哈希核验的 OpenAlex OA 与 Europe PMC OA 函数，不调用默认混合下载器或机构模块；机构访问由助手浏览器工具复用已授权会话下载，再 `acquire --import-file`，无需手装 ScanSci 或配置 Tor。全文与 SI 覆盖取决于实际来源及访问结果。

从技能目录开始：

```bash
python scripts/zotero_local.py doctor
python scripts/acquire.py --papers /path/to/work/seed --index 1 --out /path/to/work/acquisition
python scripts/ingest.py /path/to/work/acquisition/manifest.json
```

`seed` 为已有检索记录；入库默认预览，完成身份与 SI 核验后在授权范围内加 `--apply`。详见 [CLI 指南](skills/sanmu-paper-search/references/cli.md)、[本地 Zotero](skills/sanmu-paper-search/references/local-zotero.md) 与 [安装及能力边界](skills/sanmu-paper-search/README.md)。

设计参考 [ScanSci PDF](https://github.com/Rimagination/scansci-pdf)：可选来源适配在运行时提取并执行两个经 SHA-256 核验的上游纯 Python 审计函数，遵守其独立的 [Apache-2.0 许可与例外说明](https://github.com/Rimagination/scansci-pdf/blob/main/LICENSE)，不捆绑或使用其 `_core` 编译扩展；检索核心独立实现。

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

将 `skills/` 下需要的技能目录完整复制或链接到 AI 助手的技能目录，例如 `~/.agents/skills/sanmu-doc2md`。保留其中的脚本、参考文档与示例。

## 参与贡献

欢迎通过 [问题反馈](https://github.com/luffysolution-svg/sanmu-skills/issues) 提交使用问题、可复现案例和改进建议，也可以提交拉取请求。

每个技能放在 `skills/<技能名>/` 下，`SKILL.md` 中的名称与目录名保持一致。修改已有技能时保留实战经验，只调整与问题直接相关的内容。

验证 `sanmu-doc2md`：

```bash
python -m pip install -r skills/sanmu-doc2md/requirements.txt
npm --prefix skills/sanmu-doc2md/scripts install --ignore-scripts --no-audit --no-fund
python -m unittest discover -s skills/sanmu-doc2md/tests -v
python skills/sanmu-doc2md/scripts/doctor.py
```

验证 `sanmu-paper-reader`（Python 3.10+，无第三方依赖）：

```bash
python -m unittest discover -s skills/sanmu-paper-reader/tests -v
```

验证 `sanmu-paper-search`（Python 3.10+，核心无第三方依赖）：

```bash
python -m unittest discover -s skills/sanmu-paper-search/tests -v
python skills/sanmu-paper-search/scripts/paper_search.py doctor
```

## 许可证

本项目采用 [MIT 许可证](LICENSE)。
