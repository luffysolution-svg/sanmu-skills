# Zotero 同步规范与操作指南

本技能把 Zotero 当作**本地只读数据源**，写入只通过 Zotero 官方 MCP 工具执行，并回读确认。

## 1. 安全模型

1. **HTTP 只读**：`zotero_sync.py` 只调用 `127.0.0.1:23119` 的 GET 接口，不发送 PATCH/POST；`--apply` 表示"允许修改本地 Markdown 与生成操作计划"，不是"允许 HTTP 写库"。
2. **写入经 MCP**：元数据、笔记由 `update_item` / `add_note` 执行；批注由 `save_highlights` 等工具执行。计划中每条操作都有库身份、版本、前置值和依赖关系；前置条件不匹配就停止。
3. **回读才算完成**：CLI 退出码 2 表示有待执行的 MCP 操作；执行后重跑同一命令，退出码 0 且字段状态为 `verified_remote_write` 才算同步完成。
4. **三方基线**：每个文档旁保存 `.文档名.zotero-sync.json`，记录 base、pending 与文档哈希。首次同步要显式传 `--initial-direction local|remote`，因为此时还没有基线，工具无法判断哪边更新（未指定时报 `initial_direction_required`）；此后本地与远端各自修改同一字段会进入 `conflict`，不会静默覆盖。空值不会清空远端字段。
5. **Windows 换行**：受管区域按规范化换行后的内容哈希比较，CRLF/LF 变化不会被误判为人工改区域。

## 2. 字段映射

| Markdown frontmatter | Zotero | 状态 |
| :--- | :--- | :--- |
| `title` | `title` | 双向 |
| `publication_title` | `publicationTitle` | 双向 |
| `volume` / `issue` / `pages` / `date` / `doi` / `url` / `language` | 同名字段 | 双向 |
| `abstract` | `abstractNote` | 双向 |
| `keywords` | `tags` | 双向；保留 Zotero 自动标签，拒绝删除自动标签 |
| `authors` | `creators` | 只比较、不回写（显示名无法无损还原 creator 结构） |
| `has_supplement` / `supplement_to` | — | 仅 Obsidian frontmatter 关系，不写 Zotero `relations` |

## 3. CLI

```bash
# 只读审查（不写任何文件）
python scripts/zotero_sync.py "文献.md"

# 首次：指定以本地或远端为准，生成 MCP 操作计划
python scripts/zotero_sync.py "文献.md" --apply --initial-direction remote --report sync-report.json

# 执行 report 中 operations 的 MCP 工具后，再跑一次回读确认
python scripts/zotero_sync.py "文献.md" --apply --initial-direction remote --report sync-report.json

# 拉取附件 PDF 中的批注与高亮（按 annotation key 增量更新受管区域）
python scripts/zotero_sync.py "文献.md" --apply --pull-annotations --report sync-report.json
```

- `--report` 不能和输入文件、状态文件指向同一路径；已有非本工具生成的文件不会被覆盖。
- 群组库要在 frontmatter URI 中写 `zotero://select/groups/<id>/items/<KEY>`，并用 `--base-url .../api/groups/<id>`；个人库与群组库会互相校验，防止写错库。
- 退出码：`0` 已一致或已完成回读，`1` 失败/冲突，`2` 有待执行的 MCP 操作。

## 4. 阅读笔记

- 只同步 `## 阅读笔记` / `## Notes` 等标题下的正文；已存在的受管笔记通过稳定 `KB-SYNC:` 标识与 note key 更新，不重复新建。
- 用户自己创建的 Zotero 笔记不会被识别为受管笔记，也不会被覆盖。
- 受管批注区（`<!-- zotero-sync:annotations:begin -->`）与阅读笔记分离；批注增删改只更新该区域。

## 5. 附件审计

报告中的附件条目检查：是否存在、是否 PDF、能否通过本地 API 读取、SHA-256、字节数、Zotero linkMode，以及是否疑似补充材料。审计只读，不改附件。
