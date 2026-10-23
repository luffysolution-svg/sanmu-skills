# MinerU 云端 OCR API

MinerU 官方 API 可对 PDF / 图片做 OCR，同时输出文字、LaTeX 公式、表格结构和插图切分。需要 Token（读环境变量 `MINERU_API_TOKEN`）。`scripts/mineru_ocr.py` 已经封装了下面的全部流程，这里的细节主要用于排查问题和理解输出。

## 目录

- 接口契约：申请上传链接、上传、查询、下载
- 结果 zip 结构、块类型与 bbox 坐标系
- 坑：结果下载域名连不上
- 限额与本地模型

## 接口契约

### 1. 申请上传链接

```http
POST https://mineru.net/api/v4/file-urls/batch
Authorization: Bearer $MINERU_API_TOKEN
Content-Type: application/json
```

```json
{
  "model_version": "vlm",
  "enable_formula": true,
  "enable_table": true,
  "language": "ch",
  "files": [
    {"name": "01.pdf", "data_id": "ch01", "is_ocr": true}
  ]
}
```

参数要点：

- `model_version`：本流程使用 `vlm`；输出仍须核对原页。
- `enable_formula` / `enable_table`：要打开，否则拿不到公式 LaTeX 和表格结构。
- `language`：中文文档用 `ch`。
- `files[]` 里的 `is_ocr`、`page_ranges` 属于**每个文件**；`model_version`、`enable_formula`、`enable_table`、`language` 属于**顶层**。放错位置会静默忽略。
- `data_id` 是自己分配的标识，用于回传时对应文件，建议用有意义的名字。

返回 `data.batch_id` 和 `data.file_urls`（数组，顺序与 `files` 一致）。

### 2. 上传文件

对 `file_urls` 里每个 URL 做 `PUT`，body 是**原始二进制**：

```python
with open(path,'rb') as f:
    requests.put(url, data=f, timeout=300)
```

- 不带 `Authorization`（预签名 URL 自带授权，额外的认证头可能导致签名校验失败）
- 不用 `multipart/form-data`，直接传文件字节
- URL 与 `files` 按顺序一一对应

上传完成会自动创建解析任务，不需要再调接口提交。

### 3. 查询结果

```http
GET https://mineru.net/api/v4/extract-results/batch/{batch_id}
Authorization: Bearer $MINERU_API_TOKEN
```

`data.extract_result[]` 每项含 `data_id`、`file_name`、`state`、`err_msg`、`extract_progress`、`full_zip_url`。

`state` 取值：`waiting-file` → `pending` → `running` → `done` / `failed`。`running` 时 `extract_progress` 给出 `extracted_pages` / `total_pages`。

轮询间隔 10–20 秒。几百页约十几分钟。

### 4. 下载结果

`state == "done"` 后下 `full_zip_url`。**这里有个坑，见下节。**

## 结果 zip 结构

```text
<uuid>_content_list.json      # 主要数据源：每块含 type/text/bbox/page_idx
<uuid>_content_list_v2.json   # 更细的结构（标题层级、行内公式分离）
<uuid>_model.json             # 模型原始输出
<uuid>_origin.pdf             # 回传的原 PDF
full.md                       # 拼接好的 Markdown（可参考，但结构信息少）
layout.json                   # 版面分析
images/                       # 切出的插图
```

### content_list.json 的块类型

| type | 关键字段 | 说明 |
| --- | --- | --- |
| `text` | `text`, `text_level`, `bbox` | 正文。`text_level` 有值表示是标题 |
| `equation` | `text`, `text_format: "latex"` | 公式，已是 LaTeX，独立公式自带 `$$...$$` |
| `image` | `img_path`, `image_caption`, `image_footnote`, `bbox` | 插图 |
| `chart` | `img_path`, `chart_caption`, `chart_footnote`, `bbox` | 图表（曲线图等），字段名与 image 不同 |
| `table` | `table_body`(HTML), `table_caption`, `table_footnote` | 表格，body 是 HTML 需转换 |
| `list` | `list_items` | 列表 |
| `header`/`footer`/`page_number` | `text` | 核对为运行页眉、页脚或页码后过滤；误分类的实质内容应保留 |
| `page_footnote` | `text` | 保留学术注释、来源、版权说明及公式解释；只有核对为运行页脚或噪声后才过滤 |
| `ref_text` | `text` / `ref_text` | 参考文献段落，原生转为正文，不按页脚删除 |

`image` 和 `chart` 是两种类型，字段名不同（`image_caption` 与 `chart_caption`）。只处理 `image` 会漏掉所有曲线图。

### bbox 坐标系

本工具读取的 `content_list.json` 中，`bbox` 是 0–1000 的归一化坐标 `[x0, y0, x1, y1]`。换算为目标页面的像素或 PDF point：

```python
x0 = box[0] / 1000 * page_width
y0 = box[1] / 1000 * page_height
```

`layout.json` 的块框是页面点坐标，须用该页的实际宽高转换，不能直接传给装配器或 repairs：

```python
norm_box = [box_pt[0] / page_width * 1000, box_pt[1] / page_height * 1000,
            box_pt[2] / page_width * 1000, box_pt[3] / page_height * 1000]
```

先核对数据格式、该页尺寸以及 PDF 页面的方向和原点。数值都小于 1000 并不能说明它是归一化坐标，全书宽高也不能写死，因为同一本书里可能夹着横页或尺寸不同的插页。图题位置从 layout 的 `image_caption` / `chart_caption` 子块读取；题注也可能误归入对应的 footnote 子块，需回源确认。content_list 中携带题注的图块框并非题注自身的位置。

### 回源修复

修正文字、裁框、题注，以及补图、合图、拆图，都写进 `repairs.json`，格式见 [repairs.md](repairs.md)。所有 repairs 框都用 0–1000 坐标。

## 坑：结果下载域名连不上

结果 zip 托管在 `cdn-mineru.openxlab.org.cn`。**如果本机 DNS 被代理的 fake-IP 接管，这个域名会解析成 `198.18.0.62` 之类的保留地址**，表现为 Python `requests` 和 `curl` 都在 TLS 握手阶段 EOF：

```text
ssl.SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING]
curl: (35) schannel: failed to receive handshake, SSL/TLS connection failed
```

`https://mineru.net` 本身能通（所以容易误判为"网络正常"），只有这个 CDN 域名不通。

**处理**：用公共 DoH 拿真实 IP，再指定连接目标。TLS 校验保持开启（不用 `-k`），也不改系统代理设置：问题出在 DNS，不在证书，关掉校验只会引入中间人风险。

```bash
# 取真实 IP
curl -s "https://dns.alidns.com/resolve?name=cdn-mineru.openxlab.org.cn&type=A"

# 指定 IP 下载
curl --resolve cdn-mineru.openxlab.org.cn:443:<真实IP> -L -o out.zip "<full_zip_url>"
```

多个 IP 依次重试。`scripts/mineru_ocr.py` 里已实现这套回退逻辑。

其他公开 DoH 端点：`https://cloudflare-dns.com/dns-query?name=<域名>&type=A`（需 `Accept: application/dns-json`）。

## 限额

- 单文件通常 200 MB / 600 页以内
- 单批文件数有上限（约 200）
- 超限的任务会被拒绝，按批次拆分

具体数值以官方文档为准：<https://mineru.net/apiManage/docs>

## 本地模型（无 Token 时）

MinerU 也提供本地部署（`pip install mineru`），需要单独配置模型和运行环境。版本、模型和输出格式可能与云端不同，别默认两者等价；本技能的 `submit` 使用云端 API，本地结果须先核对数据结构再接入。
