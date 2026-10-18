# 本地 Zotero：查询、写入与附件

Python 3.10+ 标准库客户端，无需 MCP 或云端 API key。Zotero 桌面端需运行，并在设置中允许本机应用通信；读取通常需要 Zotero 7+，写入需要 Zotero 8+。默认只访问 `http://127.0.0.1:23119`，自定义 `--base-url` 也只接受回环 HTTP，禁止重定向。

## 读取与定位

```bash
python scripts/zotero_local.py doctor
python scripts/zotero_local.py libraries
python scripts/zotero_local.py collections
python scripts/zotero_local.py search "MatterGen"
python scripts/zotero_local.py get ITEMKEY1
python scripts/zotero_local.py children ITEMKEY1
python scripts/zotero_local.py file PDFKEY12
python scripts/zotero_local.py export ITEMKEY1 --format bibtex
```

示例 key 需替换为实际查询结果。列表返回 `items`、`truncated`、`next_start` 等字段；默认最多 1000 条、10 页，可显式调整 `--max-items`、`--max-pages`。不得用截断快照宣布未收藏，查重不完整时拒绝新建。群组库用 `--library g12345`，不能套用个人库 WebDAV 行为。

`file` 返回 `available`、`missing` 或 `not_file`。只读取 API 返回的具体文件地址，不扫描 storage。远端存在但未同步到本机的附件可能是 `missing`；客户端不代替 Zotero 从 WebDAV 拉取。正文抽取可交已有阅读工具，或在取得路径后用 PDF 工具读取。

## 写入

写命令默认预览；在用户已授权的范围内加 `--apply` 才执行。首次授权：

```bash
python scripts/zotero_local.py doctor --apply
```

Zotero 会显示独立客户端的本地授权对话框。授权信息保存到当前用户缓存，限制文件权限；不读取宿主插件密钥，不写入仓库或运行记录。仅选择一次允许时不持久化，Zotero server ID 改变会使旧会话失效。拒绝时停止，不反复请求。

```bash
# metadata.json 是 Zotero 字段对象；stable ID 用于精确查重。
python scripts/zotero_local.py upsert --data @metadata.json
python scripts/zotero_local.py upsert --data @metadata.json --apply
python scripts/zotero_local.py update ITEMKEY1 --data @fields.json --apply
python scripts/zotero_local.py update ITEMKEY1 --data '{}' --add-tag "材料生成" --apply
python scripts/zotero_local.py create-collection "材料生成" --apply
python scripts/zotero_local.py attach ITEMKEY1 "/path/to/paper.pdf" --title "Full text" --apply
python scripts/zotero_local.py note ITEMKEY1 "<p>已核对的方法与出处。</p>" --apply
```

`upsert` 精确匹配 DOI/arXiv，命中默认不改题录；只有明确需要更新时用 `--update-existing`。无稳定 ID 的新条目不能凭标题自动合并，应先核对后用明确的条目操作。`update --data` 接受字段对象，标签/集合可用增删参数；采用版本条件更新，字段并发冲突不静默覆盖。笔记输入为 Zotero HTML，不冒称 Markdown 转换。

## 附件与失败恢复

附件导入为 `imported_file`，支持主文 PDF 和 SI 的常见文件类型，由 Zotero 管理文件及个人库 WebDAV 同步。不会直接上传到 Zotero 云端文件存储或操作 WebDAV。相同文件哈希匹配现有附件时跳过；上传失败返回 `partial`，其中含父条目、附件 key、失败阶段与恢复信息。

```bash
python scripts/zotero_local.py attach ITEMKEY1 "/path/to/paper.pdf" --attachment-key PDFKEY12 --apply
```

恢复只针对同一父条目、相同文件或尚未完成的存储附件；存在冲突时停止。不同版本追加为独立附件，不覆盖原文件。不自动删除失败时已创建的记录。显示标题可通过 `update` 修改；物理重命名和原 key 内容替换不在本客户端契约内。

`ingest.py` 可直接消费获取清单：先检查所有文件哈希、父条目身份和人工核验状态，再查重导入，主文与 SI 分开命名，部分失败不记作全部成功。具体命令见 [CLI 指南](cli.md)。导入成功只证明本地写入完成，WebDAV 同步完成需另行查看 Zotero 状态。
