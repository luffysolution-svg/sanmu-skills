# 教材与学术长文档处理指引 (Book & Monograph Workflow)

长篇学术教材、专著、学位论文（通常 200–800 页，公式成千上万）与演示课件（PPTX，几十页）有本质区别。若直接套用课件流水线，会导致图文断裂、分片错位和大量标题误判。

本指南提供长篇教材与专著的分片、重组和回源复核步骤；学位论文默认使用 `research-documents.md` 中的单篇装配入口。

---

## 1. 课件模式 (Slides) vs 教材模式 (Book) 的核心差异

| 维度 | 课件模式 (`--mode slides`) | 教材/专著模式 (`--mode book`) |
| :--- | :--- | :--- |
| **主导场景** | 讲义、PPT、专题汇报（每页一张幻灯片） | 经典教材、学术专著、科研论文（长篇连贯阅读） |
| **图文排版** | 图片汇总后置顶（符合 PPT 视觉中心） | **自然流混排**：严格按段落上下文穿插图表 |
| **跨页关系** | 页面间独立，一页一个主题 | **保留段落边界**：不凭缺少句末标点自动拼接，避免误合并正文与习题 |
| **标题判定** | 宽松（页顶短文本常为幻灯片标题） | **严格**：依据章/节编号正则（如 `第X章`、`8.1`），严禁把页顶首句误升标题 |
| **表格处理** | 转为简明 Markdown 表格 | **双轨保真**：Markdown 检索文本 + 复杂大表原版扫描高清裁片 |
| **嵌套资源** | 通常无内嵌图 | 自动提取单元格内嵌套二维码、化学符号与矢量小图 |

---

## 2. 500+ 页大文档的“分片-识别-组装”标准三步法

### 第一步：智能切分与 Manifest 生成

MinerU 云端批量 API 对单文件有体积极限（通常 200 MB / 600 页以内）。几百页扫描教材建议按 80–100 页分片：

```bash
# 自动按 96 页切片，并在工作目录生成 chunks/、manifest.json 与 ids.tsv
python scripts/mineru_ocr.py split path/to/textbook.pdf --out-root ./ocr-work --chunk-size 96
```

生成的 `manifest.json` 记录了每个分片的全局起始页码：
```json
{
  "source": "path/to/textbook.pdf",
  "pages": 556,
  "chunk_size": 96,
  "parts": [
    {"id": "part01", "start_page": 1, "end_page": 96, "pages": 96},
    {"id": "part02", "start_page": 97, "end_page": 192, "pages": 96}
  ]
}
```

### 第二步：批量提交与结果轮询

```bash
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv
```

接口采用一次性整批提交，轮询等待所有批块全部下载解压完成。

### 第三步：按全书章节重组 (Book Assembly)

准备各章节的页码起止表 `chapters.json`：
```json
[
  {"id": "front", "title": "书目信息与前言", "start": 1, "end": 13, "file": "01-前言.md"},
  {"id": "ch08", "title": "第八章 电解质溶液", "start": 24, "end": 85, "file": "08-电解质溶液.md"},
  {"id": "appendix", "title": "附录", "start": 536, "end": 540, "file": "15-附录.md"}
]
```

执行全书重组：
```bash
python scripts/build_markdown.py \
    --raw ./ocr-work/raw \
    --out ./知识库 \
    --mode book \
    --manifest ./ocr-work/manifest.json \
    --chapters ./chapters.json \
    --pdf path/to/textbook.pdf \
    --keep-table-images
```

---

## 3. 常见陷阱与应对策略

### 3.1 过滤模型推理与提示词泄漏
极少数空白页或衬页中，多模态 OCR 模型可能输出评测规则说明（如 `The Ground Truth image displays...`, `According to Rule 2...`）。
* **对策**：`build_markdown.py` 内置漏词过滤，自动识别并排除此类段落；核实空白隔页并记录在构建报告中。

### 3.2 跨页续表占位块
MinerU 在遇到跨页连续大表时（如活度因子表、标准电极电势表），会将后续页内容合并到首页表格中，后页仅留一个空 table 占位块。
* **对策**：先对照原页与前页表格，确认续表内容确实已合并后再保留为空占位；不能仅凭空块推断没有数据丢失。

### 3.3 单元格内嵌二维码与图表
现代教材各章末尾附带拓展数字化资源的二维码。
* **对策**：HTML 表格转换时，优先扫描所有 `<img src="...">`，下载为独立图片资产并替换为 `![二维码](images/...)` 相对引用，避免 `cell.get_text()` 吞图。

### 3.4 复杂科学大表与环状化学式双轨保真
* **复杂大表**：Markdown 纯文本表格方便全文搜索与 LLM 提取，但深层合并单元格和密集上下标可能失真。开启 `--keep-table-images` 会同步截取原版扫描图裁片，供科学计算复核。
* **化学环状键**：对于三角形、多边形等复杂空间结构，严禁使用 `\backslash` 乱拼字符，一律降级为局域原图裁切嵌入。

### 3.5 多面板碎图自动化审查与合并 (`audit_figures.py`)
教材中一个图号常包含多个并排子图（如布拉维点阵 14 种晶胞、多状态相图曲线）。OCR 往往将其切为十几个孤立碎块，丢失 (a)(b) 标签关联。
* **对策**：使用 `python scripts/audit_figures.py <ocr-work目录> --manifest manifest.json --out-repairs repairs-draft.json` 自动探测同页多面板并计算 union bbox，经原页核验后通过 `build_markdown.py --repairs repairs.json` 一键应用高保真重裁。

### 3.6 扫描件物理边缘噪声排查与清洗（印章、条形码乱码）
真实扫描教材常在封底、章节首尾页边角或中缝装订处残留物理印章（如图书馆藏书章、防伪印戳）、印刷定位码、条形码扫描残影。
* **现象特征**：OCR 易将其强行识别为孤立乱序问号或无意义断句（如章首第一行出现“用应从请勿填清文件中？”、“条码区：2003847…”）。
* **排查策略**：
  1. 重点核验每章 Markdown 正文前 5 行与最后 5 行，检查是否存在与章节标题或上下文无关的孤立短句；
  2. 核对对应原页物理边缘区域（尤其是页面比例坐标 $y < 0.08$ 或 $y > 0.92$，对应 repairs 的 0–1000 坐标为 $y < 80$ 或 $y > 920$，以及版心之外的外切边距）。
* **清洗方案**：在 `repairs.json` 中配置精准回源修复项，将该 text 块内容设为空字符串置空（例如 `{"page":1,"bbox":[100,20,900,60],"field":"text","old":"用应从请勿填清文件中？","new":"","reason":"扫描边缘物理印章伪影；页码与框须替换为原块定位"}`），确保章首导言和正文行文不受物理扫描杂质干扰。

### 3.7 外文/英文学术专著语言模型配置 (`--language en`)
全英文经典教材（如《Atkins’ Physical Chemistry》）词汇密集且含大量专有拉丁词根。提交 MinerU OCR 识别时须显式指定 `--language en`。该参数声明源文语言；完成后仍须逐项核对英文变量、热力学符号及化学式，不能假定识别准确率必然提升。

### 3.8 双重式号（Multiple tags）与公式语义标签分离
国际理工科专著常在公式右侧同时标注物理属性名称与全局编号（例如 `Reduced variables [definition] (1C.8)`）。大模型 OCR 易将其同时生成为两个 `\tag{}`（如 `\tag{[definition]}\tag{1C.8}`），从而触发 KaTeX 语法错误。
* **对策**：重组与归一化时，确保每个独立公式环境仅保留唯一的真实章节编号 `\tag{...}`，描述性文字（如 `[definition]`）剥离为正文或行内 `\text{}` 保持物理语义。


### 3.9 页脚分类与实质脚注
`page_footnote` 可能承载学术说明、来源及公式解释，不能与 `page_number` 一并丢弃。核对原页后保留实质内容；误分类可用 `set_type:"page_footnote"` 保留脚注，或用 `set_type:"body_text"` 还原正文。去除运行页脚或扫描噪声同样须记录旧值与原页依据。
