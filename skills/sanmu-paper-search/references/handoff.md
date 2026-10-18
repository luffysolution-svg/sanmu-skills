# 全文、Zotero 与材料交接

## 本地优先

优先使用 `scripts/zotero_local.py` 连接本机 Zotero（默认 `http://127.0.0.1:23119`），列库/分类，再查条目、读元数据和附件；宿主工具可作替代。以 DOI 或稳定 ID 优先，标题仅作为辅助。记录 `library` 与 item key，附件分别保存 key、名称、类型及角色；附件记录存在仅表示已列出，需用 `file` 检查是否已在本机可用。题名包含 SI 仅是线索，需要回读确认。

不直接打开 Zotero 数据库、不遍历 storage、不将笔记批注上传外部 API。文件只按本地 API 返回的具体附件路径读取；本机缺失不代表 WebDAV 无文件，本客户端不主动拉取远端附件。无本地连接时可读取标准 JSON 导出。核心 `merge --zotero` 只用于已有条目匹配，不写库，不把仅有条目误判为已有 PDF。

写条目、附件、标签、笔记需用户已授权；CLI 默认预览，明确执行时加 `--apply`。Zotero 8+ 首次写入需要在桌面端授权独立客户端，密钥只存用户缓存，不复用宿主密钥。按稳定 ID 查重，有歧义或扫描截断时不新建；已收藏条目不自动覆盖用户题录。新增文件导入为 Zotero 存储附件，由 Zotero 沿用个人库 WebDAV 设置同步，本地导入成功不等于同步完成。修改附件显示标题不等于实体重命名，不覆盖原 PDF；集合/标签批量调整先明确范围，写后回读。查资料和离线测试不创建测试条目。

## 全文与版本

按已有附件、出版社开放版本、机构仓储、OpenAlex/S2 OA 位置和 Unpaywall 定位。各位置保留版本、许可、来源；Crossref 的 PDF/TDM 链接不自动等于 OA。预印本（如 arXiv、机构仓储 DSpace/Zenodo）可作为正式版受阻（如 401/403 订阅墙）时的开放替代，但不能无标记代替正式版，清单需保留具体版本与来源标记。

`fetch_pdf.py` 只处理用户任务中的公开 HTTPS PDF。检查 PDF header/trailer，记录原始/最终 URL、大小和 SHA-256。可选 PyMuPDF 在首屏题名区匹配完整 DOI 与标题（同时提供时均需命中，排除参考文献区），结果只代表文字层身份线索，不证明版本、内容完整或科学正确。扫描 PDF、乱码或未匹配进入人工回看，不按尺寸删除。下载拦截（如 reCAPTCHA、Cloudflare 质询、HTML 登录页）、截断与格式损坏分别通过具体 reason 记录。

学校订阅、CARSI、WebVPN 等访问依赖用户自己的授权会话。商业刊物（如 Elsevier、Wiley、Science）与 Google 学术常设 Cloudflare 质询盾或人机验证，自动化或内置浏览器若遇拦截，用户可在宿主个人日常浏览器（含机构 IP/VPN 授权）内完成验证下载，再通过 `acquire.py --import-file` 纳入清单；缺少浏览器工具、登录过期或无订阅时保留具体缺口。登录及多因素验证由用户完成，不提取浏览器 cookie 交给脚本。当前 ScanSci 适配只运行审计过的 OA 来源函数，不自动调用其机构模块或混合批量回退；后端按需自动准备，核心检索不依赖它。不要默认启动 Tor、改代理或绕过验证码，也不要把“免费可取得”与“开放许可”混为一个字段。

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
