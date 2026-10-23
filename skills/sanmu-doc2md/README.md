<div align="center">

# sanmu-doc2md

**把论文、教材和课件，整理成可长期维护的 Markdown 知识库。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![KaTeX validation](https://img.shields.io/badge/KaTeX-validation-brightgreen.svg)](https://katex.org/)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**简体中文** · [English](README.en.md) · [返回合集](https://github.com/luffysolution-svg/sanmu-skills)

[安装](#安装) · [解决什么问题](#解决什么问题) · [三种文档三条路](#三种文档三条路) · [实测案例](#实测案例) · [命令速查](#命令速查)

</div>

扫描教材、PDF 图书、理工科课件、中英文论文、学位论文，转成带高清图片、表格和可渲染 LaTeX 公式的 Markdown，供 Obsidian 阅读，也方便 AI 助手检索。

难点不在“转出来”，而在于转得对：公式语义不能变，图要完整，页码要能追回原文。所以它会尽量从源文件取最可靠的内容，拿不准的地方回原页核对，并把每一处修正都记录下来。公式编译通过只说明语法没问题，图表和科学符号仍要对照原文，最后在你实际用的客户端里看一眼。

## 安装

### 让 AI 助手安装（推荐）

把下面这段发给 Claude Code、Codex、WorkBuddy、Cursor 等 AI 编码助手：

```text
请帮我安装并配置 sanmu-doc2md 技能：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git、Node.js 和 Python，把技能装到当前助手的用户级技能目录：
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
   已有安装时先检查本地修改，避免覆盖。
2. 找到安装出来的 sanmu-doc2md 目录（里面有 SKILL.md、requirements.txt 和 scripts/），
   后面的命令都在这个目录执行，不要在合集仓库根目录执行。
3. 最好用独立的 Python 虚拟环境，安装依赖：
   python -m pip install -r requirements.txt
   npm --prefix scripts install --ignore-scripts --no-audit --no-fund
4. 检查环境（不会提交 OCR）：
   python scripts/doctor.py
   python scripts/build_markdown.py --help
5. 检查 MINERU_API_TOKEN 是否已配置，不要显示值。没配置就告诉我去 https://mineru.net 申请；
   已配置则运行 python scripts/doctor.py --require-ocr。
6. 告诉我安装位置、检查结果和还需要配置的项目。
```

### 手动安装

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-doc2md -g
```

进入安装出来的 `sanmu-doc2md` 目录（从源码运行则是 `skills/sanmu-doc2md`），以下命令都相对这个目录：

```bash
python -m pip install -r requirements.txt
npm --prefix scripts install --ignore-scripts --no-audit --no-fund
python scripts/doctor.py
```

`doctor.py` 检查 Python、Node、KaTeX 和凭证是否就绪，并试编译一个公式；它不安装任何东西，也不显示凭证值，加 `--json` 输出结构化结果。

云端 OCR 需要环境变量 `MINERU_API_TOKEN`（在 [mineru.net](https://mineru.net) 申请）。已经有 OCR 结果时，不配置 Token 也能装配和校验。

## 解决什么问题

普通 OCR 工具直接转理工科文档，常见的翻车现场：

| 问题 | 表现 |
| --- | --- |
| 公式散架 | 上下标、分式、希腊字母错乱，散成孤立字符 |
| 版面错乱 | 长教材阅读顺序颠倒，页顶的续文被当成二级标题 |
| 方括号被当链接 | `[通常为 $\mathcal{N}(0, I)$ ]` 在 Obsidian 里变成蓝色链接，公式源码裸露 |
| 标题化学式坏掉 | `$\mathrm{CO}_2$` 被剥掉定界符，只剩 `CO_2` 还触发斜体 |
| 资源丢失 | 章末二维码、复杂数据表被纯文本转换吞掉 |
| 错别字 | “轫致辐射”→“韧致辐射”，`ν`→`v`，`N₀`→`No` |
| 模型说明混进正文 | `The Ground Truth image displays...` 这类 OCR 模型的话出现在正文里 |
| 旧图越堆越多 | 调整裁框后旧切图残留，目录越来越大 |

这些规则都来自真实教材和论文的处理过程，具体做法写在技能的参考文档里。

## 三种文档三条路

教材、课件和论文的版式差别很大，套用同一套处理方式一定会出问题，所以分成三条路：

| | 教材与专著 | 课件与讲义 | 论文与学位论文 |
| --- | --- | --- | --- |
| 入口 | `build_markdown.py --mode book` | `build_markdown.py --mode slides` | `build_research_document.py` |
| 组织方式 | 按章拆分：`00-目录.md`、`01-第一章.md`… | 按讲次或课时 | 每篇一个目录：主笔记 + `00-目录.md` |
| 图文排版 | 图表按版面位置穿插进正文 | 图片集中在页首，配合要点文字 | 沿用核对过的 OCR 阅读顺序，双栏不串行 |
| 标题判定 | 只认章节编号，区分小节标题和习题题干 | 每页的主题即标题 | 文件按 `作者 - 年份 - 标题` 命名 |
| 图表 | 原 PDF 至少 3 倍重裁，多面板合并 | 优先取 PPTX 原图，过滤图内小标签 | 高置信度多面板缝合，SI 图表双链 |
| 文字与元数据 | 保留实质脚注，清理运行页眉页脚 | 用 PPT 原文校对 OCR 错字 | 完整 YAML：DOI、作者、摘要、Zotero 链接 |

## 处理流程

```text
1. 预检      看有没有文字层和内嵌原图，判断走哪条路
2. OCR       长文档按 96 页一片切开，批量提交 MinerU
3. 校对      用原生文字层 / PPT 原文校对形近字
4. 图表      优先取原图，否则从 PDF 3 倍重裁；碎图合并，表内二维码保留
5. 公式      中文进 \text、不等号保护、方括号消歧、标题公式保留
6. 装配      生成 00-目录.md 和正文，可清理不再引用的旧切图
7. 校验      结构与链接检查 + KaTeX 全量预检 + 在 Obsidian 里实际看一遍
```

## 实测案例

仓库里的案例文档记录了以下处理结果。这些数字描述的是这几份文档，不代表对任意文档的质量保证。

| 案例 | 文档 | 公式 | 图片 | 结果 |
| :--- | :--- | :---: | :---: | :--- |
| 材料分析测试方法 | 10 章、694 页 PPT 课件 | 2,118 | 199 | 公式全部通过预检，用原生文字纠正 7 处错字，重复图片去重 |
| 物理化学（第六版）下册 | 556 页全扫描教材 | 7,523 | 332 | KaTeX 7,523 / 7,523 通过，提取 28 个章末二维码 |
| 中文论文与学位论文 | 5 篇共 611 页（含 240 页博士论文） | 5,797 | 398 | 解决 Obsidian 方括号误判和标题化学式问题，安全清理 89 张旧图 |
| 英文期刊论文 | 7 篇共 87 页（Nature、Matter 等） | 876 | 114 | 双栏阅读顺序正确，Windows 长路径正常，YAML 元数据完整 |
| **合计** | **近 2,000 页** | **16,314** | **1,043** | 案例中的公式全部通过编译 |

详见 [RESULTS.md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESULTS.md) 和 [RESEARCH-VALIDATION.md](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESEARCH-VALIDATION.md)；[可复现示例](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/book-demo/demo.md)保存在仓库中，不随技能安装。

## 命令速查

```bash
# 切片与 OCR
python scripts/mineru_ocr.py split path/to/document.pdf --out-root ./ocr-work --chunk-size 96
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv

# 教材
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 --mode book \
    --manifest ./ocr-work/manifest.json --chapters ./chapters.json \
    --pdf path/to/document.pdf --keep-table-images --prune-orphans

# 课件
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode slides --source-dir ./课件 --pdf-dir ./课件PDF

# 论文
python scripts/build_research_document.py --pdf ./source.pdf --raw ./ocr-work/raw/article \
    --metadata ./metadata.json --out "./科研知识库/第一作者 等 - 年份 - 论文标题"

# 校验
python scripts/validate_output.py ./知识库 --images-dir images
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json

# 本地预览（仅监听 127.0.0.1）
python scripts/preview_server.py ./知识库 --port 8766 --katex-dir scripts/node_modules/katex/dist
```

## 输出长什么样

- **目录干净**：只有导航、正文 Markdown 和 `images/`；构建状态放在旁边的 `.<目录名>.build/`。
- **路径全相对**：图片用 `![](images/...)`，整个知识库可以随意移动、跨设备同步。
- **双轨保真**：公式是标准 LaTeX；复杂表格和环状化学结构同时保留原图裁片。
- **导航可用**：根目录的 `00-目录.md` 使用 URI 编码链接，Obsidian 里可直接跳转。
- **能追溯也不覆盖**：保留源文件、页码映射和修复依据；默认保护你手动改过的内容，实质脚注不会被当页脚删掉。

## 包含什么

```text
SKILL.md                        技能说明：路线选择、流程、经验与陷阱
references/
  book-workflow.md              长篇教材的切片、章节重组与常见问题
  research-documents.md         论文命名、元数据、SI 双链与双栏阅读顺序
  math-normalization.md         15 条公式规则，附真实前后对照
  repairs.md                    回源修复：文字、图块、题注与扫描噪声
  setup.md                      首次配置：安装依赖、申请与保存 MinerU Token
  mineru-api.md                 MinerU 接口、结果结构、坐标系与下载回退
  zotero-sync.md                Zotero 只读 API、MCP 写入与回读
scripts/
  doctor.py                     环境与凭证检查
  mineru_ocr.py                 切片 / 提交 / 轮询 / 续传 / 下载
  build_markdown.py             教材与课件装配
  build_research_document.py    论文装配
  audit_figures.py              多面板碎图审查，生成修复草稿
  normalize_math.py             公式归一化
  extract_formulas.py           抽取全部公式
  validate_output.py            结构、链接与残留检查
  validate_math.cjs             KaTeX 全量预检
  preview_server.py             本地离线预览
  zotero_export.py              Zotero 条目与附件只读导出
  zotero_sync.py                Zotero 元数据、笔记与批注同步
```

开发测试在仓库根目录运行，见[参与贡献](https://github.com/luffysolution-svg/sanmu-skills#参与贡献)。

## 许可证

[MIT](LICENSE)
