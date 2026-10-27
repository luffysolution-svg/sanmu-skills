# 三木文献检索 (sanmu-paper-search) ZCode 插件

在 ZCode / DeepSeek Harness (`dsh`) / Claude Code 对话侧边栏与 Agent 中使用 **三木文献检索与文档解析 (`sanmu-paper-search` v1.9.0)**：

## 核心特性

1. **多源检索与精确核验**：
   - 支持 **OpenAlex**、**Semantic Scholar**、**Crossref** 多源组合主题检索（`search_papers`，支持相关性/高被引/最新排序）与稳定 ID 精确核验（`lookup_paper`）。
   - 支持 **Europe PMC** 开放检索、撤稿/勘误核查，以及在详情页按需下载解压 **补充材料（SI 原图/PDF）压缩包**（`search_epmc`）。
   - 支持 **SciVerse 开放平台**（`search_sciverse`）：全文语义片段检索（Agentic RAG 返回原文匹配段落与页码/偏移）、结构化文献/学者/期刊检索（支持布尔检索、新鲜度/影响力软加权与 FWCI）、按 Unicode 码点偏移读取论文原文 Markdown 并一键提取下载正文高清原图（`content` / `resource`）、引文关系追踪（`relations`），以及 **Paper Schema** 结构化论文搜索、实验复现/方法/综述材料包构建（`schema-materials`）与跨论文证据检索（`schema-evidence`）。
   - 支持 **Exa** 自然语言语义补搜（`search_exa`）与 **Serper** Google Scholar / 专利 / 网页正文提取（`search_serper`）。
   - 支持 **AI4Scholar** S2 / Google Scholar / 专利 / PubMed 检索（`search_ai4scholar`）、科研绘图与自动引文（`ai4scholar_draw_and_cite`），以及 **easyScholar** 期刊/会议分区、IF、CCF 与预警标注（`lookup_venue_rank`）。
2. **学者消歧、引文网络扩展与原位无损浏览**：
   - 支持按姓名列候选学者、按课题方向过滤（`topic`）与排序（`sort`）、从种子论文直接锚定同名学者稳定 ID（`--from-paper`）以及拉取学者作品（`search_author`）。
   - 支持向前查参考文献（`references`）、向后查施引文献（`citations`）与横向相似推荐（`recommendations`）（`explore_related`），当 Semantic Scholar 受出版商协议限制或限流时自动通过 Crossref + OpenAlex 补齐。
   - 卡片点击「参考文献 / 施引 / 相似 / 作者」时支持**原位内联抽屉展开**，不丢失当前检索列表，并配有结果浏览历史栈（`⬅ 后退 / ➡ 前进`）。
3. **Obsidian 风格实时物理关系图谱与图谱情报**：
   - 内置 60fps 物理弹簧力导向引文/共著星系图谱与年份演进时间轴，自动识别 **共被引基石文献 (Co-cited Hub)** 与 **跨源桥梁文献 (Bridge Paper)** Top 5 情报，支持拖拽节点牵引邻居自由流动、悬停聚光灯高亮与多跳引文融合。
4. **双通道 API 配置（免重启即配即用）**：
   - 可在 ZCode **设置 → 插件** 中配置 `politeEmail`、`mineruApiToken`、`sciverseApiKey`、`ai4scholarApiKey`、`easyscholarSecretKey`、`openalexApiKey`、`s2ApiKey`、`exaApiKey`、`serperApiKey` 与默认下载/解析目录；
   - 也可直接在右侧面板顶部点击 **「⚙ 设置」** 随时修改保存，自动继承本机已有环境变量，保存后立即生效。
5. **本机 Zotero 双向联动、浏览器 PDF 认领与三木精读交接**：
   - 自动探测本机 Zotero（`127.0.0.1:23119`）标出已收录论文及所属分类；
   - 支持单篇或批量一键存入指定 Zotero 分类（自动上传附加已下载的本地 PDF，并将课题标签 `user_tags` 写入标签、随手批注 `user_note` 创建为 Zotero 子笔记）、**一键撤销导入（`undo_zotero_save`）**及桌面端直接定位打开；
   - 支持一键认领浏览器刚下载到本机 `Downloads` 的 PDF（`claim_browser_pdf`，校验首屏 DOI/标题后重命名归档），以及一键唤起 `/sanmu-paper-reader` 执行图文精读与 SI 复现评估。
6. **学术文档解析与 Obsidian 知识库入库工作台 (`sanmu-doc2md`)**：
   - 面板内置第 6 个主导航 Tab **「📝 文档解析」**，并在论文卡片与详情页提供 **「📝 转 Markdown 入库」** 一键联动；
   - 支持三路输入源：**系统文件管理器原生弹窗选择任意 PDF / PPTX**（`pick_system_path`，自动探测同目录关联的 `-SI.pdf` 或 `.pptx`）、**从本机 Zotero 文库直接点选文献与本地 PDF/SI 附件**（`list_zotero_pdf_items`）、**从已获 PDF 检索卡片一键带入**；
   - 支持三种文档模式与三类引擎（`run_doc2md` / `manage_doc2md`）：**科研论文 + SI 双向 `[[WikiLink]]` 双链入库**、**PPTX/PDF 课件讲义转笔记（PPTX 原文自动校对错字 + 内嵌原图提取）**、**长篇教材智能切片与按章拆分**；支持自由选择或弹窗指定存放目录、MinerU 断点续传（`resume`）、全量 KaTeX 公式校验、一键启动本地 KaTeX 浏览器渲染预览（`preview_server.py`）以及一键移交 AI 精读或回源修复。
