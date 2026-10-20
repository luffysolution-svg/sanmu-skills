---
name: sanmu-doc2md
description: 将 PDF、PPTX 和扫描文档转为保留图表、LaTeX 公式与来源信息的 Markdown 知识库。用于教材分章、课件转笔记、论文与学位论文入库、图表或公式回源修复、主文与补充材料关联，以及 Zotero 文献与批注导出或同步。
---

# 文档转 Markdown 知识库 (sanmu-doc2md)

将整批文档（尤其包含复杂公式、图表、跨页表格的理工科教材、学术论文或扫描课件）整理成干净、自包含、长期可维护的 Markdown 知识库。产出同时面向人类专注阅读与智能体（Agent/RAG）精准检索。

---

## 1. 先判断，再动手（Preflight）

从技能目录执行下文 `scripts/` 命令。先运行 `python scripts/doctor.py`，需要云端 OCR 时再加 `--require-ocr`；确认目标客户端（Obsidian 使用 MathJax，本地工具使用 KaTeX 预检）。执行前检查以下四项：

1. **源文件有无原生文字层？**
   - 用 PyMuPDF 的 `page.get_text()` 或 `python-pptx` 检查。
   - 有文字时先区分原生文本与旧 OCR 层；可靠原生文字可作校对基准，旧 OCR 层仍须核对，不能仅凭字符数视为真值；
   - 若未提取到文字，结合原页确认是否为扫描件；依赖 OCR 时在交付说明中注明来源与限制。
2. **源文件有无内嵌原图？**
   - PPTX 优先提取 `shape.image.blob`，PDF 检查 `page.get_images()`。
   - 可独立复原整幅图时优先内嵌原图；多对象矢量图和扫描页按完整区域裁切，不能用单张位图替代整幅图。
3. **识别文档形态并执行架构分流：**
   - **长篇专著/扫描教材 (`--mode book`)**：多章节连续行文，保持原有多章节划分组织架构（`01-第一章.md`、`02-第二章.md`、`00-目录.md`），采用**几何自然流混排**（严禁将图强制提前）、**严格章节判定**（严禁将页顶首句误升 H2）、**保守保留段落边界**。详见 `references/book-workflow.md`。
   - **课件幻灯片/期末讲义 (`--mode slides`)**：一页一个独立主题，保持按讲次/课时的组织架构，图文视觉集中，优先图置顶并与 PPT 原生文字对齐。
   - **学术科研文献 (Research Document)**：期刊论文、学位论文、预印本、科技研报（来自 Zotero 或独立 PDF/Word 衍生文件）。执行单篇独立文献一体化架构：输出**学术标准命名** `{{firstCreator}} - {{year}} - {{title}}.md`、生成配套 `00-目录.md` 与 `images/`；提取并注入元数据 frontmatter；双栏排版默认保留经核对的 OCR 阅读流。详见 `references/research-documents.md`。
4. **安全交付目标路径：**
   - 默认输出到**全新的独立目录**（如 `<源目录>/<书名>-知识库` 或 `<文献学术标准名>`），严禁破坏覆盖原始文件。
   - 默认只更新未被人工修改的生成文件，拒绝清空目录。遇到人工修改先保留并确认差异，不用 `--overwrite` 绕过保护。默认只交付导航、正文 Markdown 与图片；构建状态及质检报告留在交付目录之外。

---

## 2. 核心执行工作流

### 步骤 1：智能切分与 OCR 识别

超长教材（建议 >200 页或 >100 MB）先切片。提交入口会强制预检：PDF 有效性、加密、页数 >600、体积 >200 MB、单批 >200 份均拒绝；分片同时受页数与体积约束：

```bash
# 1. 超长 PDF 自动切片（以 96 页为例）并生成 manifest.json 与 ids.tsv
python scripts/mineru_ocr.py split <源PDF路径> --out-root ./ocr-work --chunk-size 96

# 2. 批量提交 MinerU 云端 API（模型 vlm，开启公式与表格）
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv
```

- **英文教材**：在 `submit` 命令中显式传入 `--language en`，随后核对专有名词、变量和化学式；语言参数不保证识别无误。
- **坐标系统**：本工具读取的 `content_list.json` 使用 **0–1000 归一化坐标**，裁图时按实际页面宽高换算；`layout.json` 的页面点坐标须先按该页宽高归一化，不能混用或写死整本书尺寸。定位图题时使用 layout 中题注本身的框，不能用所属图块框代替（见 `references/mineru-api.md`）。
- **网络回退**：若遇 `cdn-mineru.openxlab.org.cn` TLS 握手 EOF，脚本已内置公共 DoH 获取真实 IP 并使用 `curl --resolve` 绕过 fake-IP 劫持（详见 `references/mineru-api.md`）。

### 步骤 2：用原生文字校正 OCR 错字（PPTX/有文字层 PDF）

OCR 会出现形近字、同音字或英文字母误识（如“轫致辐射”误为“韧致辐射”、“ν”误为“v”）。
- 当前装配脚本支持 PPTX 按文本块位置重叠度（`overlap > 0.5`）与相似度（`sim >= 0.9`）校对；有文字层 PDF 需另外核对，不能假设已自动替换。
- 差异较大者记录在 `build-report.json` 的 `review` 字段中留待复核。

### 步骤 3：高清图表提取与资产保真

提取优先级：
1. **源文件内嵌原生图片**（能完整复原可见图时优先）；
2. **按完整图表区域从原 PDF 至少 3 倍缩放高清裁切**（扫描件或由多个矢量/位图对象组成的图）；
3. **表格内嵌图片提取**：遇到包含 `<img src="...">` 的表格（如各章拓展学习二维码），自动转存为图片并替换为 Markdown 相对引用，避免丢失；
4. **特殊复杂结构保真**：对三角形化学键等无法用标准 LaTeX 表达的二维空间图，以及深层合并的复杂科学实验表，开启 `--keep-table-images` 保留高清原版裁图双轨备查。

- **去重**：按 SHA-256 / SHA-1 内容哈希去重，减少挂图讲解产生的重复资产。
- **图内标签过滤**：文字块完全落在图片区域内（重叠度 > 0.6）且小于 12 字符的标签，丢弃不重复为正文。
- **多面板碎图自动缝合与 S 编号独立隔离**：
  - **高置信度自动缝合**：`--auto-repairs`（单篇论文流程默认开启）只合并同页、同图号或明确 `(a)/(b)` 面板、空间相邻且中间无正文的碎图；必须同时提供原 PDF 与 manifest。跨栏、远距离、无题注或缺面板证据的候选只导出人工草稿，不自动合并；
  - **原图优先**：PDF 中可独立复原且未旋转、裁剪或与其他对象重叠的单张位图直接取原图，其余按完整区域至少 3 倍页面渲染裁切；PPTX 图片带裁剪、旋转、翻转或位于组合内时回退为 PDF 裁切，避免把幻灯片上隐藏的区域恢复出来；
  - **S 编号独立隔离**：自动识别 `Figure S1`、`Fig. S1`、`Table S1`、`Scheme S1`、`Supplemental/Supplementary Figure 1`、`Supplementary Note 1` 与 `附图 S1`，统一纳入 `supplemental:` 独立命名空间，与正文 `Figure 1` 区分，仍须核对编号和归属；
  - **人工审核与兜底审计**：使用 `python scripts/audit_figures.py <raw目录> [--manifest manifest.json] --out-repairs repairs-draft.json` 导出草稿；经原页核验后可通过 `--repairs` 显式覆盖。

### 步骤 4：科学公式严谨归一化

保留行内 `$...$` 与独立行 `$$...$$`，按以下顺序处理；具体前后对照见 `references/math-normalization.md`。

1. 运行装配器的保守归一化；检查中文 `\text{}`、化学式 `\mathrm{}`、粘连命令（如 `\vertz` → `\vert z`、`\vertj` → `\vert j`、`\vertHg` → `\vert Hg`、`\cdotkg` → `\cdot kg`）及不等号保护（`<`/`>` → `\lt `/`\gt `，`<<`/`>>` → `\ll `/`\gg `；HTML 表格在解析前实体化）。
2. 标题默认保留行内公式（如 `$\mathrm{CO}_2$`）；纯文本位置仅作等义 Unicode 转换（如 `CO₂`），不要留下 `CO_2`。图片 alt 用纯文本，复杂公式另放独立图注。
3. 核对 `[Fe(CN)6](aq)` 等化学物态伪链接，再放入数学环境；解释性方括号发生客户端冲突时，核对后改为 `［...］`，保留真实引用、链接与数学分组。
4. 将未知命令、重复式号、跨正文括号和化学结构列为回源候选；括号配平只能定位疑点。核对原页后用 `--repairs` 修复，不猜测编号、物态或物理含义；二维结构失真时保留原版裁图。
5. 在步骤 6 编译全部公式，并在步骤 7 验证实际客户端。语法通过不证明语义与原文一致。

### 步骤 5：组织正文结构与知识库装配

```bash
# 1. 课件模式（PPTX/幻灯片）：
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode slides --source-dir ./课件 --pdf-dir ./课件PDF

# 2. 教材/专著模式（多批块长文档，保持多章节架构）：
python scripts/build_markdown.py --raw ./ocr-work/raw --out ./知识库 \
    --mode book --manifest ./ocr-work/manifest.json --chapters ./chapters.json \
    --pdf <整本PDF路径> --keep-table-images

# 3. 学术科研文献模式（期刊/学位论文/独立PDF，统一学术标准命名）：
python scripts/build_research_document.py --pdf <论文PDF> --raw <OCR目录> \
    --out "./科研知识库/第一作者 等 - 年份 - 论文标题" \
    --metadata ./library.json --id 条目key \
    --si-doc "第一作者 等 - 年份 - 论文标题 - SI"
```

- **架构分流原则**：
  - **专著教材与复习讲义**：保持原有多章节划分（`01-第一章.md`、`02-第二章.md`、`00-目录.md`），采用几何自然流混排（垂直量化阶梯 $\Delta y = 25$），核对插图位置及跨页段落。
  - **科研学术文献**：统一默认采用学术标准命名 `{{firstCreator}} - {{year}} - {{title}}.md`，可用 `--file` 指定文件名；独立 PDF 与 Word 衍生文件只从标题、DOI、首页与文件属性等可核对证据提炼字段，不把 PDF 创建时间或 Office 用户名当出版信息；单篇输出主笔记、`00-目录.md`（URI 编码安全跳转）与 `images/`。
  - **补充材料 (SI) 跨文档双向网络**：传入 `--si-doc` 自动将 `Figure S1`、`Fig. S1`、`Table S1`、`Scheme S1`、`Supplemental/Supplementary Figure 1`、`Supplementary Note 1` 等引用解析为双链：图表指向 SI 中实际生成的 `## Figure S1` 等标题；`Note` 只链接整篇 SI（构建器不生成 Note 标题，避免死锚点）；现有链接、行内代码、围栏代码自动跳过。传入 `--supplement-to` 在 SI 顶部注入回链卡片，并在 frontmatter 建立 `has_supplement` / `supplement_to`。
- **段落与来源**：保守保留 OCR 段落与跨页边界，避免误拼题目、公式及新段落；保留源文件、manifest 全局页码和修复依据，元数据只采纳可核对证据。
- **标题与习题精准消歧**：区分名词性小节编号（如 `1. 晶胞概念` 归入 H3）与指令性习题题干（如 `1. 计算这个数`、以 `。？！` 结句或位于习题区者保留为列表/正文），层级结构严谨；拆分章名与传入标题一致时去重；`page_footnote` 中的学术注释、来源和公式说明，以及整页图均须保留；只有核对为运行页脚、页码或噪声后才过滤。`ref_text` 原生转为正文段落。
- **干净交付**：默认只生成导航、正文 Markdown 和图片；构建状态位于输出旁的 `.<目录名>.build/`，可用 `--state-dir` 指定外部工作目录。仅需清理旧切图时用 `--prune-orphans` 按 `.build-ownership.json` 核对，未引用资产默认保留。
- **可选检索**：仅当用户明确要求后续 Agent 检索、RAG 时添加 `--agent-index`，生成 `agent/` 分块与原页映射。需要主题入口和分层问答时，可交给 `sanmu-paper-reader` 的 `topic_tools.py` 复用；未安装该技能不影响转换。路由、摘要和学习笔记单独保存，正文与原页映射保持独立。
- **回源修复**：`--repairs repairs.json` 接受 `[{"page":1,"bbox":[100,200,800,300],"field":"text","old":"原识别","new":"核对后的内容","reason":"原页核对依据"}]`；可附 `set_type:"body_text"` 强制正文或 `set_type:"page_footnote"` 保留脚注，`text` 则仍参与标题判定。补原图使用 `action:"add_image"`、裁切 `bbox` 和 `after:{"bbox":[...],"text":"原始锚点"}`。旧值或定位不匹配立即停止，审计留在外部构建状态。
- **图块修复**：`action:"update_image"` 用原 `page+bbox` 和完整 `old` 图块快照核对后，原子修改 `new_bbox`、`image_caption` / `image_footnote`；`add_image` 也支持题注，配合 `skip` 处理确认后的合图、拆图。新增或改框必须从原 PDF 重裁，修复图块不再自动合并。格式及 chart 字段规则按需读取 `references/mineru-api.md` 的“回源修复图块”。
- **过滤模型泄漏**：自动清除 `The Ground Truth...` 等模型推理遗留文本。
- **扫描边缘噪声**：核对章首、章末与页边，确认印章或条形码伪字符后，用 `--repairs` 的 `old`、`new:""` 和 `reason` 精准清除。实战例与参数见 `references/book-workflow.md`。

### 步骤 6：交付前全量校验（核心质量门禁）

对输出执行结构校验并编译全部公式；修复失败项后重跑。KaTeX 是语法预检，不能替代原页或目标 MathJax 核验；遇引擎差异须记录原式、来源和实际显示结果，未解决项明确列出。

```bash
# 1. 结构与资源链接完整性检查
python scripts/validate_output.py ./知识库 --images-dir images [--resource-root <父目录>]

# 2. 抽取全部公式并执行本地 KaTeX 语法预检
python scripts/extract_formulas.py ./知识库 --out formulas.json
node scripts/validate_math.cjs formulas.json
```

跨目录 SI 双链必须传 `--resource-root`（主文与 SI 的共同父目录），否则目标缺失会直接报错而不是放宽。检查围栏未闭合错误与图片遮蔽警告；合法图片语法示例须标注代码语言（如 `markdown`）。校验器会自动遮蔽公式区域，避免将化学式等配位方括号误报为链接失效。源图片候选数、解析图片数与有效引用数含义不同，不能直接要求相等。未引用资产会列出路径，默认保留；重建可用新目录核验，不清空已有知识库。

### 步骤 7：浏览器实际渲染沙箱验收

启动本地离线渲染服务，通过自动化或人工查看确认图片正常、公式报错为 0：

```bash
python scripts/preview_server.py ./知识库 --port 8766 --katex-dir scripts/node_modules/katex/dist
```
- 打开 `http://127.0.0.1:8766/` 验证 `document.documentElement.dataset.ready === "true"` 且 `.math-error` 数量为 0；检查各章实际图片数量与加载状态。加载成功只证明文件可显示，异常图仍须逐幅对照原页确认面板、题注和裁框完整。
- **客户端真实渲染终验**：**“公式语法编译通过 ≠ 客户端显示正常”**。本地 KaTeX 沙箱通过后，在实际宿主客户端（Obsidian 使用 MathJax）中核验阅读与实时预览视图，检查伪链接、裸公式和图片。无法访问客户端时，明确标注终验尚未完成。
- **Obsidian 客户端兼容提示**：在瞬间批量写入数百张新插图时，Obsidian 后台文件监视器（File Watcher）可能存在索引延迟，偶见“找不到图片”占位提示。先核对路径与图片解码；路径正确时可尝试重载或重启 Obsidian，再确认是否恢复。

### 步骤 8：Zotero 同步（只读 API + MCP 写入）

Zotero 本地 HTTP API 在本技能中**只读**；所有写入经 Zotero 官方 MCP 工具执行，并回读确认：

```bash
# 1. 只读对比（默认不写任何文件）
python scripts/zotero_sync.py "./科研知识库/第一作者 等 - 年份 - 标题/第一作者 等 - 年份 - 标题.md"

# 2. 首次同步：显式选择以哪一方为准，生成 MCP 操作计划
python scripts/zotero_sync.py "文献.md" --apply --initial-direction remote \
    --report zotero-sync-report.json

# 3. 用 MCP 执行 report 中的 operations（update_item / add_note），
#    再跑同一命令回读；退出码 0 且状态 verified_remote_write 才算完成
python scripts/zotero_sync.py "文献.md" --apply --initial-direction remote \
    --report zotero-sync-report.json

# 4. 拉取 PDF 批注（按 annotation key 增量更新受管区域）
python scripts/zotero_sync.py "文献.md" --apply --pull-annotations \
    --report zotero-sync-report.json
```

- 退出码：`0` 已一致/回读通过，`1` 冲突或失败，`2` 有待执行的 MCP 操作。
- 三方基线与 pending 保存在文档旁的 `.文档名.zotero-sync.json`；首次未指定方向会报 `initial_direction_required`。
- 群组库必须同时匹配 frontmatter URI 与 `--base-url /api/groups/<id>`；个人库与群组库不会互相误路由。
- 只更新带稳定 `KB-SYNC:` 标识的受管笔记和 `zotero-sync:annotations` 受管区域，用户自建笔记与正文不受影响。字段映射与限制见 `references/zotero-sync.md`。

---

## 3. 边界与易错场景

- **自动处理与回源修复**：语法归一化限于明确规则；标题层级、段落拼接、图题归属、式号和化学结构以原页为准，不用计数或编译结果代替内容判断。
- **人工内容保护**：不清空输出目录，不覆盖人工改动；原始 OCR 保留，修复通过带旧值守卫的 `--repairs` 记录。Zotero HTTP 只读，MCP 写入前核对库身份、版本和前置值，完成后回读；冲突不自动合并，空值不清空远端。
- **Windows 长路径**：只给最终写入加 `\?\`、枚举却用普通 `rglob`，会漏读超长路径。枚举、读取、写入、原子替换与校验须使用同一套安全路径，目录访问错误不能静默吞掉。
- **多面板与复杂结构**：仅按步骤 3 的证据自动合并；跨栏、缺题注或图号有歧义时保留人工草稿。不要用单张位图替代完整多对象图，也不要用字符拼凑失真的环状化学键。
- **表格与模型残留**：转换表格时保留内嵌图片和二维码，保留实质脚注；核对并清除 `The Ground Truth image displays...` 等模型说明，不能把它当原文。

---

## 4. 按需参考文档

- `references/research-documents.md` — 学术文献（期刊/学位论文/预印本/独立PDF）标准命名、元数据frontmatter、双栏阅读流与验收规范
- `references/zotero-sync.md` — Zotero 同步（只读 API、三方基线、MCP 操作计划与回读、批注增量、附件审计）规范
- `references/book-workflow.md` — 500+ 页学术专著与教材的切片、全局映射与分章节重组指南
- `references/math-normalization.md` — 公式归一化规则与真实例子
- `references/mineru-api.md` — MinerU 官方契约、数据结构、归一化坐标与 DoH 连接策略
- [在线实战案例](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESULTS.md) — 《材料分析测试方法》PPT 课件与《物理化学（第六版）下册》556 页教材
- [在线科研文献案例](https://github.com/luffysolution-svg/sanmu-skills/blob/main/examples/sanmu-doc2md/RESEARCH-VALIDATION.md) — 中文学位论文、英文双栏论文与 SI 实测
