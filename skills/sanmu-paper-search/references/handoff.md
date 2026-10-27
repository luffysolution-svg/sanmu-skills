# 全文、Zotero 与材料交接

## 本地优先

有宿主 Zotero 工具时按该工具的连接流程列库/分类，再查条目、读元数据和附件。以 DOI 或稳定 ID 优先，标题仅作为辅助。记录 `library` 与 item key，附件分别保存 key、名称、类型及角色；附件记录存在仅表示已列出，能否本地打开需通过宿主回读。题名包含 SI 仅是线索，需要回读确认。

不直接打开 Zotero 数据库、不遍历 storage、不将笔记批注上传外部 API。无宿主工具可请求/读取标准 JSON 导出。核心 `merge --zotero` 只用于已有条目匹配，不写库，不把仅有条目误判为已有 PDF。

写条目、附件、标签、批注需用户已授权并通过宿主工具执行；批量改动范围应明确，回读核对。查资料和运行测试不需要写入测试条目。

## 全文与版本

按已有附件、出版社开放版本、机构仓储、OpenAlex/S2 OA 位置和 Unpaywall 定位。各位置保留版本、许可、来源；Crossref 的 PDF/TDM 链接不自动等于 OA。预印本可作为可读替代，但不能无标记代替正式版。

可选 Serper `search` 可定位出版社、仓储和 SI 页面，`webpage --url URL` 可提取公开网页文字及链接，见 [Serper 指南](serper.md)。可选 SciVerse `content` 接口可按 `doc_id` 与 Unicode 码点偏移读取已收录论文的原文 Markdown 切片，并通过 `resource` 下载其中 `![alt](file_name)` 引用的正文图表；注意 `offset/limit` 均按 Unicode 码点计数并只能用返回的 `next_offset` 翻页，读取切片或结构化摘要在未完成实际精读前仍保持 `read=false`。网页提取可能只有摘要、参考文献或部分内容；HTTP 200、`markdown` 字段或附件链接都不能证明已取得完整主文/SI。先回查目标身份、正文与实际附件，再更新获取状态；`webpage` 不代替 PDF 下载或机构授权访问。

`fetch_pdf.py` 只处理用户任务中的公开 HTTPS PDF。检查 PDF header/trailer，记录原始/最终 URL、大小和 SHA-256。可选 PyMuPDF 在首屏题名区匹配完整 DOI 与标题（同时提供时均需命中，排除参考文献区），结果只代表文字层身份线索，不证明版本、内容完整或科学正确。扫描 PDF、乱码或未匹配进入人工回看，不按尺寸删除。下载失败、HTML 非 PDF、身份待核验分别记录。

学校订阅、CARSI、WebVPN 等访问依赖用户自己的授权会话。外部下载工具作为可选后端，事先核对来源策略和实际可用工具；不默认启动 Tor、改代理、导入 cookie 或绕过验证码。不要把“免费可取得”与“开放许可”混为一个字段。

## Supporting Information

从已经核对的主文出版社页查附件区。记录每个实际附件的标题、URL、类型、版本、获取状态与主文 DOI；包括 PDF、表格、压缩包、视频及数据链接。不要仅找一个 `supporting.pdf` 就宣布全部 SI 已取得。链接无效、需登录、下载失败与尚未查找分别报告。

交给转换流程前检查文件签名、可打开性与内容身份；附件内应能找到主文题名/作者/编号等对应证据。软件或数据集另保留 DOI/版本与许可。压缩包不执行其中代码，解包防路径越界。

## 交接清单示例

```json
{
  "paper": {"doi": "10.xxxx/example", "title": "Exact title"},
  "zotero": {"library": "user", "key": "ABCDEFGH"},
  "main": {"status": "file_obtained", "path": "sources/paper.pdf", "identity": "needs_review"},
  "supporting_information": {"status": "not_retrieved", "files": []},
  "screening": {"basis": "abstract", "reason": "研究对象与目标反应一致，方法仍需阅读全文核对"},
  "next": "核对主文和SI后转换；读方法与图表验证相关性"
}
```

示例字段值是占位符。`read` 需要阅读记录才能成立。简单问答可直接使用同等内容的表格，不强制生成额外 JSON。长期归档时清单与文件放同一用户工作目录，相对路径交接；私有快照、PDF、批注和测试结果不提交开源仓库。

`sanmu-doc2md` 负责保真转换与主文/SI双链；`sanmu-paper-reader` 负责读证据、机理、复现与条件比较。本技能输出候选与获取状态，不生成未经全文核验的科学结论。
