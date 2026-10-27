# 三木文献检索 (sanmu-paper-search) ZCode 插件

在 ZCode / DeepSeek Harness (`dsh`) / Claude Code 对话侧边栏与 Agent 中使用 **三木文献检索 (`sanmu-paper-search` v1.7.0)**：

## 核心特性

1. **多源检索与精确核验**：
   - 支持 **OpenAlex**、**Semantic Scholar**、**Crossref** 多源组合主题检索（`search_papers`）与稳定 ID 精确核验（`lookup_paper`）。
   - 支持 **Europe PMC** 开放检索、撤稿/勘误核查，以及在详情页按需下载解压 **补充材料（SI 原图/PDF）压缩包**（`search_epmc`）。
   - 支持 **Exa** 自然语言语义补搜（`search_exa`）与 **Serper** Google Scholar / 专利 / 网页正文提取（`search_serper`）。
   - 支持 **AI4Scholar** S2 / Google Scholar / 专利 / PubMed 检索（`search_ai4scholar`）、科研绘图与自动引文（`ai4scholar_draw_and_cite`），以及 **easyScholar** 期刊/会议分区、IF、CCF 与预警标注（`lookup_venue_rank`）。
2. **学者消歧、引文网络扩展与原位无损浏览**：
   - 支持按姓名列候选学者、从种子论文直接锚定同名学者稳定 ID（`--from-paper`）以及拉取学者作品（`search_author`）。
   - 支持向前查参考文献（`references`）、向后查施引文献（`citations`）与横向相似推荐（`recommendations`）（`explore_related`），当 Semantic Scholar 受出版商协议限制或限流时自动通过 Crossref + OpenAlex 补齐。
   - 卡片点击「参考文献 / 施引 / 相似 / 作者」时支持**原位内联抽屉展开**，不丢失当前检索列表，并配有结果浏览历史栈（`⬅ 后退 / ➡ 前进`）。
3. **Obsidian 风格实时物理关系图谱**：
   - 内置 60fps 物理弹簧力导向引文/共著星系图谱与年份演进时间轴，支持拖拽节点牵引邻居自由流动、悬停聚光灯高亮与多跳引文融合。
4. **双通道 API 配置（免重启即配即用）**：
   - 可在 ZCode **设置 → 插件** 中配置 `politeEmail`、`openalexApiKey`、`s2ApiKey`、`exaApiKey`、`serperApiKey`、`easyscholarSecretKey`、`ai4scholarApiKey` 与默认下载目录；
   - 也可直接在右侧面板顶部点击 **「⚙ 设置」** 随时修改保存，自动继承本机已有环境变量，保存后立即生效。
5. **本机 Zotero 双向联动（查重 / 一键入库 / 一键撤销）与引文复制**：
   - 自动探测本机 Zotero（`127.0.0.1:23119`）标出已收录论文及所属分类；
   - 支持单篇或批量一键存入指定 Zotero 分类（自动上传附加已下载的本地 PDF）、**一键撤销导入（`undo_zotero_save`）**及桌面端直接定位打开；
   - 支持文献收藏篮、三格式引文实时预览与一键复制（GB/T 7714 / BibTeX / APA），以及一键发送给对话 AI 对比精读。
