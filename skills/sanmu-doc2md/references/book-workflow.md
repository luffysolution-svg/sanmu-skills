# 教材与长篇专著

教材和专著动辄几百页、上千个公式，和几十页的课件完全是两回事。直接套用课件的处理方式，会出现图文脱节、分片页码错位、大量正文被误判成标题等问题。本文讲长篇文档的切片、重组和回源复核。学位论文虽然也长，但默认走 [research-documents.md](research-documents.md) 的单篇入口。

## 目录

- 课件模式与教材模式的区别
- 切片、识别、重组三步
- 常见问题

## 课件模式与教材模式的区别

| | 课件 `--mode slides` | 教材 `--mode book` |
| --- | --- | --- |
| 典型材料 | 讲义、PPT、专题汇报 | 教材、专著 |
| 图文排版 | 图片集中放在页首，贴合 PPT 以图为主的版式 | 按版面位置和上下文，把图表穿插进正文 |
| 跨页 | 每页独立 | 保留段落边界，不因缺句末标点就跨页拼接，以免正文和习题粘在一起 |
| 标题判定 | 宽松，页顶短句通常就是标题 | 严格，只认章节编号（如 `第X章`、`8.1`），页顶首句通常只是上一页的续文 |
| 表格 | 简明 Markdown 表格 | Markdown 表格 + 复杂大表的原版裁图 |
| 内嵌资源 | 一般没有 | 提取单元格里的二维码、化学符号和矢量小图 |

## 切片、识别、重组三步

### 1. 切片

MinerU 云端接口对单文件有上限（通常 200 MB、600 页以内），几百页的扫描教材按 80–100 页一片比较稳妥：

```bash
python scripts/mineru_ocr.py split path/to/textbook.pdf --out-root ./ocr-work --chunk-size 96
```

工作目录里会生成 `chunks/`、`ids.tsv` 和记录每片全局起始页的 `manifest.json`：

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

后面重组和 repairs 都依赖这份页码映射，别手动改它。

### 2. 提交与轮询

```bash
python scripts/mineru_ocr.py submit --out-root ./ocr-work --id-file ./ocr-work/ids.tsv
```

整批一次提交，脚本会轮询到所有分片下载解压完毕。

### 3. 按章节重组

准备章节页码表 `chapters.json`：

```json
[
  {"id": "front", "title": "书目信息与前言", "start": 1, "end": 13, "file": "01-前言.md"},
  {"id": "ch08", "title": "第八章 电解质溶液", "start": 24, "end": 85, "file": "08-电解质溶液.md"},
  {"id": "appendix", "title": "附录", "start": 536, "end": 540, "file": "15-附录.md"}
]
```

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

拆分后的章名和 `chapters.json` 里的标题一致时会自动去重。

## 常见问题

### 模型说明混进正文

少数空白页或衬页上，多模态 OCR 会吐出评测规则之类的文字（`The Ground Truth image displays...`、`According to Rule 2...`）。`build_markdown.py` 会识别并过滤这类段落；交付前仍要核对这些空白隔页，并在构建报告里留下记录。

### 跨页续表只剩空块

遇到跨页大表（活度因子表、标准电极电势表），MinerU 会把后续页的内容并进首页表格，后页只留一个空的 table 占位块。先对照原页和前页确认内容确实已经并入，再保留这个空块；单凭“是空块”不能断定没有丢数据。

### 单元格里的二维码和小图

很多教材在章末放拓展资源二维码。转换 HTML 表格时，先扫出所有 `<img src="...">`，存成独立图片并替换成 `![二维码](images/...)`；直接 `cell.get_text()` 会把图吞掉。

### 复杂大表与环状化学结构

- **复杂大表**：Markdown 表格方便搜索和 LLM 提取，但多层合并单元格和密集上下标容易失真。`--keep-table-images` 会同时保留原版裁图，供核对数值用。
- **环状化学键**：三元环、多边形骨架这类二维结构，用 `\backslash` 拼字符只会得到既无法解析也看不懂的结果，改用原图局部裁切。

### 多面板图被拆碎

一个图号下常有多个并排子图（14 种布拉维点阵、多状态相图），OCR 往往切成十几块孤立碎片，丢掉 (a)(b) 之间的关联。用 `audit_figures.py` 找出同页多面板并计算并集框，核对原页后用 repairs 重新裁切，命令和格式见 [repairs.md](repairs.md)。

### 扫描边缘的印章和条形码

扫描教材的封底、章首章末、页边和中缝常残留藏书章、防伪印戳、印刷定位码、条形码残影。OCR 会把它们认成孤立的问号或无意义短句，例如章首第一行出现“用应从请勿填清文件中？”“条码区：2003847…”。

排查方法：

1. 看每章正文的前 5 行和最后 5 行，找与标题和上下文无关的孤立短句；
2. 到原页对应的边缘区域核对：页面比例 y < 0.08 或 y > 0.92（repairs 的 0–1000 坐标下即 y < 80 或 y > 920），以及版心以外的切边。

确认后用 repairs 把这块文字清空，写法见 [repairs.md](repairs.md) 的“清除扫描边缘噪声”。

### 英文专著

全英文教材（如 *Atkins' Physical Chemistry*）术语密集，提交 OCR 时加 `--language en`。这个参数只是告诉模型源文语言，不保证准确率一定提高，识别后仍要逐项核对变量、热力学符号和化学式。

### 一个公式两个 `\tag`

国外理工科教材常在公式右侧同时标注属性名和编号，如 `Reduced variables [definition] (1C.8)`。OCR 容易生成 `\tag{[definition]}\tag{1C.8}`，KaTeX 直接报错。每个公式只保留真实的编号 `\tag{1C.8}`，`[definition]` 这类说明移到正文或行内 `\text{}`。

### 实质脚注被当页脚删掉

`page_footnote` 里常有学术说明、来源和公式解释，不能和 `page_number` 一起丢掉。核对原页后保留实质内容；分类错了可以用 repairs 的 `set_type` 纠正。删除运行页脚或扫描噪声时，同样要在 repairs 里留下原值和依据。
