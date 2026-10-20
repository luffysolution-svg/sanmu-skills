# 科研与学术文献处理规范

本指南面向期刊论文、学位论文、会议论文、预印本（arXiv）及独立科技研报的知识库整理。支持 Zotero 本地库导出条目与独立 PDF / Word 衍生文档，保留全文 LaTeX 公式、高清科学插图、跨页表格与规范元数据 frontmatter，并全量执行 KaTeX 编译门禁与来源核对。

---

## 1. 适用范围与架构分流

针对不同文献形态，系统采取严格的架构分流：

| 文献类型 | 处理入口 | 核心组织架构 | 命名规范 |
| :--- | :--- | :--- | :--- |
| **学术科研文献**<br>（期刊/学位论文/预印本/研报） | `scripts/build_research_document.py` | 单篇独立文献体系：主笔记 Markdown、`00-目录.md` 导航索引、`images/` 资产目录及外部构建状态 | **学术标准命名**：<br>`{{firstCreator}} - {{year}} - {{title}}.md` |
| **专著图书 / 扫描教材**<br>（数百页大部头教材） | `scripts/build_markdown.py --mode book` | 严格章节划分架构：`01-第一章.md`、`02-第二章.md`、`00-目录.md` | 按教材大纲/章节层级命名 |
| **课件幻灯片 / 讲义**<br>（PPT / 期末复习讲义） | `scripts/build_markdown.py --mode slides` | 页面主题化架构：单讲 / 单课时文件或整套幻灯片混排 | 按讲次或主题命名 |

---

## 2. 输入源预检与元数据提取

输入支持两种模式：**Zotero 库管理文献** 与 **独立文件（PDF / Word 转 PDF）**。

### 模式 A：Zotero 关联文献
1. 优先使用 Zotero 客户端或本地 API 选定条目与附件。
2. 若需批量导出条目与附件元数据，运行只读导出适配器：
   ```bash
   python scripts/zotero_export.py --out ./research-work
   ```
   - 适配器通过本地 API 精确导出附件，严禁遍历 `storage` 或操作 `zotero.sqlite`。
   - `library.json` 保留条目、附件、SHA-256 哈希及元数据状态；`ids.tsv` 用于批量提交 OCR。

### 模式 B：独立 PDF 与 Word 衍生文件
- 无需依赖 Zotero，直接传入本地文件（如 `--pdf ./paper.pdf`）。
- **元数据智能推断与提取**：
  1. **文件名模式解析**：若文件名符合 `[作者] - [年份] - [标题]` 常见模式（如 `Vaswani et al - 2017 - Attention Is All You Need.pdf`），自动解析第一作者（英文提取姓氏并补全 `等`，中文取全名）、4 位公历年份与标题；
  2. **文档内嵌属性**：读取 PDF properties / XMP（如 Word 导出时记录的 Author、Title、CreationDate），提取字段候选；Office 用户名和 PDF 创建时间不能直接当作作者与出版年份，须以首页、DOI 或其他可核对出版信息确认；
  3. **公开 DOI 查询（可选）**：通过 `--crossref` 显式开启 Crossref 校验补全，仅当 DOI 与规范化标题双重吻合时才采纳字段；
  4. **规范 JSON 覆盖（可选）**：可通过 `--metadata ./meta.json` 显式传入由原文核对的字段。

---

## 3. OCR 识别与标准命名装配

### 步骤 1：MinerU OCR 识别
将文献提交 MinerU 云端 API（英文论文须指定 `--language en`）：
```bash
python scripts/mineru_ocr.py submit --out-root ./research-ocr \
    --id-file ./research-work/ids.tsv --language en
```
若已有解析结果，通过 `--raw` 传入单篇结果目录或 raw 根目录，无需重复提交。

### 步骤 2：单篇知识库装配
运行科研装配入口：
```bash
python scripts/build_research_document.py \
    --pdf ./research-work/sources/Noh-2019-InverseDesign.pdf \
    --raw ./research-ocr/raw/条目key \
    --metadata ./research-work/library.json --id 条目key \
    --out "./科研知识库/Noh 等 - 2019 - Inverse Design of Solid-State Materials" \
    --state-dir "./research-state/Noh-2019"
```

### 核心装配规则：
1. **统一学术标准命名**：
   - 默认且标准的文件名与文件夹名采用 Zotero 模板规范：
     `{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}{{ title truncate="100" }}`
   - 示例：`Noh 等 - 2019 - Inverse Design of Solid-State Materials via a Continuous Representation.md`
   - **多作者规则**：英文作者提取第一作者姓氏（LastName），中文作者提取全名；多于 1 位作者时自动追加 ` 等`；
   - **标题安全清洗与截断**：冒号 `:` 替换为 ` - `，文件系统保留字符 `\/*?"<>|` 替换为 `_`，合并空白并截断至最多 100 字符；
   - **自定义覆盖**：若需指定特定文件名，可传入 `--file <自定义文件名.md>`。
2. **补充材料与附录规范 (Supplemental Information / SI)**：
   - 期刊文献常附带独立补充材料（如 Elsevier 的 `mmc1.pdf`、Nature/Science 的 `Supporting Online Material`）；
   - 出版社制作的 SI PDF 属性 title 经常直接沿用主文标题；系统内置附录标题变体容错机制（自动支持 `Supplemental Information`、`Supporting Information`、`Supplementary Material`、`Appendix` 等后缀），放行合法衍生版本；
   - **跨文档自动双链与 S 编号隔离**：
     - 图表审核引擎自动识别 `Figure S1`、`Fig. S1`、`Table S1`、`Scheme S1`、`Supplemental/Supplementary Figure 1`、`Supplementary Note 1` 与 `附图 S1`，统一纳入 `supplemental:` 独立命名空间，与正文图号区分，随后核对编号和归属；
     - 构建主文时传入 `--si-doc <SI文档名>`，自动将正文中的 SI 图表引用转换为带锚点的 Obsidian 双链 `[[<SI文档名>#Figure S1|Figure S1]]`，并在文末生成补充材料导读。`Note` 引用链接到整篇 SI，因为构建器不会为 Note 生成独立标题；现有链接、行内代码与围栏代码保持不变。构建 SI 时传入 `--supplement-to <主文档名>`，顶部自动注入导读卡片，且两者在 frontmatter 中分别写入 `has_supplement` 与 `supplement_to`。跨目录校验主文时必须传入 `--resource-root`（主文与 SI 的共同父目录）。
   - 交付建议：输出至独立目录（如 `{{firstCreator}} - {{year}} - {{title}} - Supplemental Information`），并通过 `--file` 传入专有文件名，避免与主篇同名冲突。
3. **双栏阅读流保持**：
   - 科研论文默认使用 `--reading-order source`，保留 OCR 按物理页核对的阅读流，避免双栏排版因几何高度重叠而发生栏间交错。
4. **Windows 路径安全**：
   - 写入使用临时文件加原子替换；枚举、读取、写入与校验统一使用扩展路径（`\\?\`，UNC 为 `\\?\UNC\`）。只给写入加前缀而枚举用普通 `rglob` 会漏掉超长路径文件，因此必须整条链路一致。
5. **Obsidian 链接兼容**：
   - 生成的 `00-目录.md` 采用标准 URI 编码（`urllib.parse.quote`），原生支持 Obsidian 双链补全与跨平台文件打开。

---

## 4. frontmatter 与学术元数据规范

主笔记头部输出严谨的 YAML frontmatter，面向人类阅读与智能体/RAG 检索：

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

### 字段约束与隐私安全：
- 严格过滤内部临时标识：严禁在 frontmatter 中输出 `zotero_item_key` 或 `zotero_pdf_uri`；
- 仅在原 PDF 与 Zotero 库匹配校验一致时输出可跳转条目的 `zotero_item_uri`；
- 作者列表保留完整姓名与原始顺序，不得在 YAML 中擅自截断为 et al.；
- 摘要完整保留原文段落与换行，严禁用 AI 提要替换原文摘要。

---

## 5. 高清图表、公式与回源修复体系

1. **图块审查与多面板重组**：
   运行图块审查脚本检测同页破碎的多面板图：
   ```bash
   python scripts/audit_figures.py ./research-ocr/raw/条目key \
       --out-repairs ./repairs-draft.json --out-report ./figure-audit.json
   ```
   - 顶刊论文常见 (A)、(B) 并排多面板插图（如潜空间映射图、相图与实验表征曲线），OCR 常将无独立题注的子面板拆碎为散落图块；
   - 审查脚本自动计算 union bbox 外接矩形并提出 `update_image`（合并大图与完整题注）和 `skip`（过滤多余碎块）建议；
   - 核对原页确认后，直接传入 `--repairs` 执行，系统将从原 PDF 重新裁切确认后的合图；旧资产默认保留，需清理时使用 `--prune-orphans` 按所有权账本核对。
2. **回源修复（零污染原则）**：
   - 审查后若发现文字误识、图题错挂或多面板拆分，在 `repairs.json` 中配置坐标与原文快照守卫：
     ```json
     [
       {
         "page": 1,
         "bbox": [100, 200, 800, 300],
         "field": "text",
         "old": "OCR原始误识文本",
         "new": "原文核对正确文本",
         "reason": "对照第1页原版文献校正"
       }
     ]
     ```
   - 通过 `--repairs ./repairs.json` 注入修复，原始 OCR 数据文件零修改，所有更正由外部账本可追溯复现。
3. **科学结构保真**：
   - 复杂表格同时保留 Markdown 表格与原版高清裁图（`--keep-table-images`）；
   - 二维化学结构与图中复杂式子保留原版高清切片，用于核对空间关系；编译通过不证明科学含义无误。

---

## 6. 质量门禁与验收交付

科研装配入口自动运行结构检查与公式预检；随后还需执行浏览器与目标客户端验收：

1. **自动化检查**：
   - **结构完整性**：验证 Markdown 语法与所有切片图片的存在性及可解码性；
   - **公式全量 KaTeX 编译**：抽取文中全部行内与独立公式，执行 KaTeX 语法预检，失败项回源修复后重跑。Obsidian 使用 MathJax；引擎支持差异须核对原式与目标客户端并记录，不能改写科学含义来消除报错。
2. **本地沙箱浏览器验收**：
   启动离线预览服务：
   ```bash
   python scripts/preview_server.py ./科研知识库 --port 8766 \
       --katex-dir scripts/node_modules/katex/dist
   ```
   核验 `document.documentElement.dataset.ready === "true"` 且 `.math-error` 数量为 0；再在实际 Obsidian 的阅读与实时预览视图检查 MathJax 公式、图片、链接与图题。无法访问目标客户端时标注终验尚未完成。
3. **交付与安全合规**：
   - 输出交付目录仅包含干净的标准命名 Markdown、`00-目录.md` 与高清 `images/`；
   - 保留源文件、页码映射和修复依据；保留 `page_footnote` 中的实质学术说明与来源。默认保护人工修改，先核对差异再决定合并。
   - 构建缓存、中间数据与所有权账本（`.build-ownership.json`）严格隔离于阅读目录之外；
   - 用户私有 PDF 原文、个人文献库元数据、未公开研究数据严禁提交至版本控制仓库。
