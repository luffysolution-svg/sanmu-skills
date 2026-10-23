# 期刊论文与学位论文

适用于期刊论文、学位论文、会议论文、预印本和科技研报。来源可以是 Zotero 条目，也可以是单独的 PDF 或由 Word 导出的 PDF。目标是每篇论文得到一个独立目录：一份按学术惯例命名的主笔记、`00-目录.md` 和 `images/`，公式、插图、跨页表格和元数据都保留下来。

## 目录

- 输入：Zotero 条目或独立文件
- OCR 与装配
- 命名规则
- 补充材料（SI）
- frontmatter
- 图表与回源修复
- 验收与交付

## 输入：Zotero 条目或独立文件

### Zotero 条目

先在 Zotero 客户端或通过本地 API 选定条目和附件。需要批量导出时运行只读导出器：

```bash
python scripts/zotero_export.py --out ./research-work
```

导出器通过本地 API 精确取附件，不遍历 `storage` 目录，也不碰 `zotero.sqlite`，因为直接读写数据库可能和正在运行的 Zotero 冲突、损坏库文件。产物里 `library.json` 保存条目、附件、SHA-256 和元数据状态，`ids.tsv` 可直接用于批量 OCR。

### 独立 PDF / Word 导出文件

不需要 Zotero，直接用 `--pdf ./paper.pdf`。元数据按以下来源依次推断，只采信能核对的证据：

1. **文件名**：符合 `作者 - 年份 - 标题` 模式时（如 `Vaswani et al - 2017 - Attention Is All You Need.pdf`）自动解析。英文作者取第一作者姓氏并补 ` 等`，中文作者取全名，年份取 4 位公历年。
2. **PDF 属性 / XMP**：读取 Author、Title、CreationDate 作为候选。Office 用户名常常是排版人员，PDF 创建时间也不是出版年份，这两项要用首页、DOI 等出版信息确认后才能用。
3. **Crossref（可选）**：`--crossref` 开启 DOI 查询，DOI 和规范化后的标题都对上才采纳返回字段。
4. **手工元数据（可选）**：`--metadata ./meta.json` 传入对照原文核对过的字段。

## OCR 与装配

英文论文提交 OCR 时加 `--language en`：

```bash
python scripts/mineru_ocr.py submit --out-root ./research-ocr \
    --id-file ./research-work/ids.tsv --language en
```

已有解析结果的话，直接把单篇结果目录或 raw 根目录传给 `--raw`，不必重复 OCR。

```bash
python scripts/build_research_document.py \
    --pdf ./research-work/sources/Noh-2019-InverseDesign.pdf \
    --raw ./research-ocr/raw/条目key \
    --metadata ./research-work/library.json --id 条目key \
    --out "./科研知识库/Noh 等 - 2019 - Inverse Design of Solid-State Materials" \
    --state-dir "./research-state/Noh-2019"
```

- **双栏阅读顺序**：默认 `--reading-order source`，沿用 OCR 按物理页核对过的阅读流。改用 `geometry` 时，双栏论文容易因为两栏高度重叠而左右交错。
- **多面板自动缝合**：默认开启，`--no-auto-repairs` 可关闭；合并条件见 SKILL.md 的图表一节。
- **Windows 路径**：写入走临时文件加原子替换；枚举、读取、写入和校验统一使用扩展路径（`\\?\`，UNC 为 `\\?\UNC\`）。只在写入时加前缀、枚举仍用普通 `rglob`，会漏掉超长路径下的文件，所以整条链路要一致。
- **Obsidian 链接**：`00-目录.md` 里的链接用标准 URI 编码（`urllib.parse.quote`），在 Obsidian 和其他平台都能正常跳转。

## 命名规则

文件名和文件夹名默认沿用 Zotero 的模板：

```
{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}{{ title truncate="100" }}
```

例如 `Noh 等 - 2019 - Inverse Design of Solid-State Materials via a Continuous Representation.md`。

- **作者**：英文取第一作者姓氏，中文取全名；作者多于 1 位时加 ` 等`。
- **标题清洗**：冒号 `:` 换成 ` - `，文件系统保留字符 `\/*?"<>|` 换成 `_`，合并连续空白，截断到最多 100 个字符。
- **自定义**：用 `--file <文件名.md>` 指定。

## 补充材料（SI）

期刊常附独立的补充材料（Elsevier 的 `mmc1.pdf`、Nature/Science 的 Supporting Online Material）。出版社制作的 SI PDF，其属性里的标题经常直接沿用主文标题，所以元数据比对时会容许 `Supplemental Information`、`Supporting Information`、`Supplementary Material`、`Appendix` 等后缀变体。

**主文与 SI 双向链接**：

- 构建主文时传 `--si-doc <SI文档名>`，正文里的 SI 图表引用会变成带锚点的 Obsidian 双链，如 `[[<SI文档名>#Figure S1|Figure S1]]`，锚点指向 SI 中实际生成的 `## Figure S1` 等标题；文末附补充材料导读。已有链接、行内代码和代码块保持不变。
- `Supplementary Note` 的引用链接到整篇 SI，因为构建器不会为 Note 生成独立标题，强行加锚点会成为死链。
- 构建 SI 时传 `--supplement-to <主文档名>`，顶部插入回链卡片。两边的 frontmatter 分别写入 `has_supplement` 和 `supplement_to`。
- SI 建议放进单独的目录（如 `作者 - 年份 - 标题 - Supplemental Information`），并用 `--file` 指定文件名，避免与主文重名。
- 主文和 SI 不在同一目录时，校验要加 `--resource-root <共同父目录>`。

S 编号的识别范围和独立命名空间见 SKILL.md 的图表一节。

## frontmatter

主笔记开头的 YAML 同时服务人类阅读和 Agent 检索：

```yaml
---
title: "Inverse Design of Solid-State Materials: via a Continuous Representation and Benchmarks"
document_type: journal_article
authors:
  - "Juhwan Noh"
  - "Jaehoon Kim"
date: "2019-11"
publication_title: "Matter"
volume: "1"
issue: "5"
pages: "1370-1384"
doi: "10.1016/j.matt.2019.08.017"
abstract: "Inverse design of materials with targeted properties..."
zotero_item_uri: "zotero://select/library/items/ABCD2345"
---
```

- 不输出 `zotero_item_key`、`zotero_pdf_uri` 这类内部临时标识，它们换台电脑就失效，还会暴露本地库结构。
- 只有原 PDF 和 Zotero 条目核对一致时，才写入可跳转的 `zotero_item_uri`。
- 作者列表保留全名和原始顺序，不截成 et al.，否则后续检索和引用都会丢信息。
- 摘要保留原文段落和换行，不要用 AI 生成的提要替换。

## 图表与回源修复

顶刊论文常见 (A)(B) 并排的多面板图，OCR 容易把没有独立题注的子面板拆成散块。先出审查草稿：

```bash
python scripts/audit_figures.py ./research-ocr/raw/条目key \
    --out-repairs ./repairs-draft.json --out-report ./figure-audit.json
```

脚本会计算并集框，建议用 `update_image` 合并整图和完整题注、用 `skip` 跳过碎块。核对原页后作为 `--repairs` 传入，脚本从原 PDF 重新裁切。文字误识、题注错配也用同一套 repairs，格式见 [repairs.md](repairs.md)。原始 OCR 文件始终不改，所有更正都记在外部，可追溯也可重放。

复杂表格加 `--keep-table-images`，同时保留 Markdown 表格和原版裁图。二维化学结构和图中的复杂式子保留原图切片，用于核对空间关系；公式编译通过不代表科学含义没错。

## 验收与交付

论文入口会自动运行结构检查和 KaTeX 预检，之后还要在浏览器和目标客户端里看：

1. **自动检查**：Markdown 结构、所有图片能否找到并正常解码、全部公式的 KaTeX 预检。失败项回原页修复后重跑。Obsidian 用 MathJax，遇到引擎差异时记录原式和实际显示，不要改写科学含义来消除报错。
2. **本地预览**：

   ```bash
   python scripts/preview_server.py ./科研知识库 --port 8766 \
       --katex-dir scripts/node_modules/katex/dist
   ```

   确认 `document.documentElement.dataset.ready === "true"` 且 `.math-error` 数量为 0，再到 Obsidian 的阅读视图和实时预览里检查公式、图片、链接和题注。无法访问目标客户端时，注明终验未完成。
3. **交付内容**：交付目录只放标准命名的 Markdown、`00-目录.md` 和 `images/`；构建缓存、中间数据和所有权账本 `.build-ownership.json` 放在阅读目录之外。保留源文件、页码映射和修复依据，以及 `page_footnote` 里的实质说明。默认保护人工修改，先比较差异再决定合并。
4. **隐私**：用户的 PDF 原文、个人文献库元数据和未公开数据不要提交到版本控制仓库。
