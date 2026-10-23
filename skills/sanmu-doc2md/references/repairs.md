# 回源修复（repairs.json）

OCR 结果有错时，不直接改原始数据，而是把核对过的修改写进 `repairs.json`，构建时通过 `--repairs` 传给 `build_markdown.py` 或 `build_research_document.py`。这样原始 OCR 一直保持原样，每处修改都有原值、新值和依据，可以复查也可以重放。

## 目录

- 通用约定
- 修正文字与块类型
- 清除扫描边缘噪声
- 修正图块：裁框与题注
- 补图、合图、拆图
- 从审查草稿开始

## 通用约定

- `page` 是从 1 开始的全书 PDF 页码（教材模式下由 manifest 换算到全局页码）。
- 所有 `bbox` 都用 0–1000 归一化坐标。从 `layout.json` 取来的页面点坐标要先按该页宽高换算，见 [mineru-api.md](mineru-api.md) 的坐标系一节。
- `old` 是守卫：它必须和原块完全一致，修复才会生效。对不上、定位有歧义或同一块被改两次，构建会直接停止。这样可以防止旧修复被套用到重新 OCR 后已经变了的内容上。
- `reason` 写清楚依据，例如“对照第 9 页原图确认左侧子图被漏裁”。
- 修复审计留在外部构建状态里，不进交付目录。

## 修正文字与块类型

```json
[{"page": 1, "bbox": [100, 200, 800, 300], "field": "text",
  "old": "原识别", "new": "核对后的内容", "reason": "原页核对依据"}]
```

块被分错类时可以加 `set_type`：

- `"set_type": "body_text"`：强制作为正文；
- `"set_type": "page_footnote"`：作为脚注保留，适合被误判成页脚、其实承载学术说明的块；
- 普通 `text` 类型仍会参与标题判定。

## 清除扫描边缘噪声

藏书章、防伪印戳、条形码残影会被 OCR 认成孤立的乱句。排查时重点看每章 Markdown 的前 5 行和最后 5 行，再到原页的上下边缘（0–1000 坐标下 y < 80 或 y > 920）以及版心外侧核对。确认是噪声后，把 `new` 设为空字符串：

```json
{"page": 1, "bbox": [100, 20, 900, 60], "field": "text",
 "old": "用应从请勿填清文件中？", "new": "",
 "reason": "扫描边缘物理印章伪影"}
```

示例里的页码和框是占位值，实际要换成原块的定位。

## 修正图块：裁框与题注

`update_image` 用原块的 `page + bbox` 定位，并用完整的 `old` 快照核对：

```json
[{
  "page": 9,
  "bbox": [400, 100, 900, 400],
  "action": "update_image",
  "old": {
    "type": "image",
    "img_path": "images/original.jpg",
    "bbox": [400, 100, 900, 400],
    "image_caption": ["图 1-2 原识别题注"],
    "image_footnote": []
  },
  "new_bbox": [100, 100, 900, 400],
  "image_caption": ["图 1-2 核对后的题注"],
  "reason": "原页确认左侧子图被漏裁，并核对图下题注"
}]
```

- `old` 必须完整列出原块的五个字段；缺失的 `img_path` 写 `""`，缺失的题注和脚注写 `[]`。
- 原块是 `chart` 时，`old` 里用 `chart_caption`、`chart_footnote`；顶层的新值仍写 `image_caption`、`image_footnote`，脚本会写回对应字段。
- 只改题注时可以省略 `new_bbox`。改了裁框就必须能从原 PDF 重新裁切，裁切失败会直接停止，不会退回旧图。

## 补图、合图、拆图

- **补图**：`"action": "add_image"`，给出裁切 `bbox`，用 `"after": {"bbox": [...], "text": "原始锚点"}` 决定插在哪段之后；同样可以带 `image_caption` / `image_footnote`（字符串列表）。
- **跳过碎块**：`"action": "skip"`，`old` 写该块的原始 `text`（没有就写 `""`）。
- **合图**：用 `update_image` 把保留的那块改成核对后的并集框，其余碎块用 `skip`。
- **拆图**：用 `update_image` 收窄原块，再按锚点顺序 `add_image` 补上其余部分。

经过修复的图块边界就此固定，不会再被自动合并。同一位置重复补图的冲突检查仍然有效，不能拿“已跳过”来绕开。

## 从审查草稿开始

多面板碎图不必手写 repairs，先让脚本出草稿：

```bash
python scripts/audit_figures.py <raw目录或ocr-work> --manifest manifest.json \
    --out-repairs repairs-draft.json --out-report figure-audit.json
```

脚本会找出同页的多面板碎块，计算并集框，并给出 `update_image` + `skip` 的建议；加 `--confident-only` 只导出高置信度的条目。没有 manifest 时只支持单篇文档。对照原页确认、改好之后，再作为 `--repairs` 传入，脚本会从原 PDF 重新裁切合并后的整图。旧图默认保留，需要清理时用 `--prune-orphans`。
