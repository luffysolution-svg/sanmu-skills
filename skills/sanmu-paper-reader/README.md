<div align="center">

# sanmu-paper-reader

**读懂论文、对齐证据，把材料与化学文献积累成可复用的研究知识。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/luffysolution-svg/sanmu-skills/actions/workflows/tests.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**简体中文** · [English](README.en.md) · [返回合集](https://github.com/luffysolution-svg/sanmu-skills)

[安装](#安装) · [使用场景](#使用场景) · [效果展示](#完整效果展示) · [学科检查](#材料与化学的五类检查) · [验证范围](#验证与限制)

</div>

面向已有论文、SI、Zotero附件与解析后的Markdown，支持单篇精读、跨文献对比、跨学科迁移、跨主题综合和知识库问答。核心是把论断链接到已读的证据，并保留条件、局限和研究疑问。按任务需要启用图文对照（折叠 Callout 与双链悬停）、复现准备 SOP 草案、Mermaid 机理图、SI 定向检索、Benchmark 比较与经授权的 Zotero 标签更新；保留证据来源、单位、工况及作者报告与研究假设的区别。

## 完整效果展示

以发表在顶级材料期刊 *Advanced Materials* (2024, 36, 2313513) 的光催化产氢论文作为精读测试样本，在 Obsidian 知识库中的实际交付效果如下：

<details open>
<summary><b>1. 笔记结构与多层大纲导航视图（点击展开/折叠）</b></summary>

![Obsidian 笔记结构与导航](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_overview.png)
</details>

<details>
<summary><b>2. 复现准备 SOP 草案与配方卡（按原文记录参数与缺项）</b></summary>

![Obsidian SOP 配方卡](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_sop_card.png)
</details>

<details>
<summary><b>3. 原生 Mermaid 反应机理与电荷动力学流程图</b></summary>

![Obsidian Mermaid 流程图](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_mermaid_mechanism.png)
</details>

<details>
<summary><b>4. 双链气泡悬停与折叠抽屉子图精准对照（Sub-figure Crop）</b></summary>

![Obsidian 图文证据对照与切片子图](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_visual_evidence.png)

![Obsidian 折叠图表与 Figure 3 对照](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_figure_comparison.png)
</details>

<details>
<summary><b>5. 50+ 页 SI 定向探针与催化性能 Benchmark 横向对照</b></summary>

![Obsidian SI 定向探针与 Benchmark 对照](https://raw.githubusercontent.com/luffysolution-svg/sanmu-skills/main/docs/showcases/sanmu-paper-reader/images/obsidian_si_probe.png)
</details>

## 安装

### 让AI助手安装（推荐）

复制以下指令：

```text
请帮我安装并配置 sanmu-paper-reader 文献解读技能：
https://github.com/luffysolution-svg/sanmu-skills

1. 检查 Git、Node.js，通过下面的命令安装到当前助手的用户级技能目录：
   npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-reader -g
   选择当前助手的安装目标；已有安装先检查本地修改。
2. 阅读安装目录里的 SKILL.md 和 README.md。
3. 核心解读由当前助手完成，不需要单独模型API或deep-research。证据、主题与基准工具使用Python 3.10+标准库；需要SI探针、PDF附图提取或子图裁切时，在独立虚拟环境执行 python -m pip install -r requirements.txt，安装PyMuPDF与Pillow。
4. 从包含SKILL.md的技能目录运行 python scripts/evidence_tools.py --help，确认脚本入口。安装不要求运行源码仓库的开发测试。
5. 我提供Markdown时直接阅读；提供PDF时检查当前宿主的PDF读取能力；提供Zotero条目时检查Zotero连接。需要扫描件解析时再配置可用OCR工具。
6. 不显示凭证值。告诉我技能位置、入口检查结果，以及哪些可选读取能力可用。
```

### 命令行安装

```bash
npx skills add luffysolution-svg/sanmu-skills --skill sanmu-paper-reader -g
```

核心解读与证据、主题、基准工具使用Python 3.10+标准库。`si_probe.py`、`extract_si_figures.py`需要PyMuPDF，`crop_subfigures.py`需要Pillow；使用这些功能时，从技能目录在独立虚拟环境安装：

```bash
python -m pip install -r requirements.txt
```

技能安装器不会自动安装上述依赖。PDF读取、OCR、Zotero还可由宿主或可选工具提供，均不强制绑定某个平台。若从本地未发布源码安装，把完整的 `skills/sanmu-paper-reader` 目录复制或链接到助手的技能目录；远程命令只能安装已经发布到仓库的版本。

## 使用场景

| 场景 | 示例请求 | 主要产物 |
| --- | --- | --- |
| 单篇 | 精读这篇光催化论文，解释Fig.4和Fig.5，区分实验观察与机理推断。 | 核心论断、证据、方法缺口与研究启发 |
| 跨文献 | 比较MatterGen和GNoME，重点看稳定性定义、参照凸包与实验验证口径。 | 带条件的比较表、一致与分歧 |
| 跨学科 | 将材料生成模型的方法迁移到催化剂设计，指出概念对应和验证缺口。 | 适用条件映射、失配风险和验证方案 |
| 跨主题 | 把晶体生成、缺陷调控和光热催化组织成一个研究问题，区分文献依据与新假设。 | 主题联系、证据缺口和阅读路径 |
| 知识库问答 | 我现有文献中，哪些支持“缺陷一定提高产氢”？找反例和条件限制。 | 有出处的回答与检索范围 |

不会因为问一个具体问题就生成一组长文档。完整精读默认生成独立阅读笔记；跨文献和长期积累可附证据表，来源文本与用户原有见解分开保存。

## 材料与化学的五类检查

- **合成与表征**：原料、组成、物相、热处理、样品身份及结构证据，避免把间接表征写成唯一机理。
- **催化与电化学**：光源、波长、反应介质、温度、负载量、参比电极、归一化与空白对照；光催化与电催化按实际研究分别检查。
- **DFT与计算**：泛函、U/自旋、k点、收敛、表面/缺陷、能量参照、自由能修正；计算稳定不等同动力学可行或可合成。
- **光谱与机理**：峰归属、拟合、对照、时间尺度、原位状态与替代解释；PL强度和寿命需联系具体过程判断。
- **性能比较**：指标定义、单位、条件、样本和不确定度；材料ML额外区分训练/测试、数据泄漏、预测/DFT/实验验证与统计分母。

参考文件按场景与学科两层组织，主文件只加载当前任务需要的部分。跨学科与跨主题有独立方法，不用单篇总结模板拼接。

## 离线证据与科研辅助工具

从技能目录运行：

```bash
# 1. 证据核验与清单生成
python scripts/evidence_tools.py inventory ./sources --out ./work/sources.json
python scripts/evidence_tools.py check ./work/evidence.json --root ./sources --out ./work/check.json

# 2. 有文字层的 SI 定向检索与附图/整页预览
python scripts/si_probe.py ./sources/paper-SI.pdf --search "TEOA,xenon,Hubbard"
python scripts/extract_si_figures.py ./sources/paper-SI.pdf --out-dir ./images/SI

# 3. 大图子面板精准切片 (Sub-figure Crop)
python scripts/crop_subfigures.py ./images/master_fig.png --out-dir ./images/subfigures --spec-json ./crops.json

# 4. 性能 Benchmark 结构化横向入库
python scripts/benchmark_collector.py add ./benchmarks.csv --json-entry '{"doi":"10.1002/adma.202313513","condition_id":"420nm","activity_rate":"10.71","activity_unit":"mmol g-1 h-1","source":"SI p.4 Table S1"}'

```

| 工具 | 做什么 | 注意 |
| --- | --- | --- |
| `evidence_tools.py` | `inventory` 记录 Markdown 来源的相对路径、哈希和章节；`check` 检查证据表的字段、路径、哈希和引文 | 不联网，不生成科学结论；证据表按[证据契约](references/evidence-contract.md)由助手读完后填写；已有文件内容不同时拒绝覆盖 |
| `si_probe.py` | 在有文字层的 SI 里定向搜索参数和题注 | 扫描件要先 OCR |
| `extract_si_figures.py` | 按 Figure S / Fig. S 题注提取附图 | 清单的 `extraction_mode` 区分 `embedded_image` 和 `page_render`；同页多图共享一张整页图，题注可能漏检，回原页确认 |
| `crop_subfigures.py` | 按坐标把多面板大图切成单个子图 | 讲到哪个面板就放哪张图 |
| `benchmark_collector.py` | 记录带单位、工况和出处的性能数据，可导出 Markdown | 用 `record_id` 或“DOI + `condition_id`”区分工况；只更新显式给出的字段，`""` / `null` 表示清空；匹配含糊时报错且不写盘，不会自动合并历史记录 |

## 可选主题路由与分层读取

已有教材、课件、论文或 SI 解析稿需要反复问答时，可用标准库工具建立主题入口，保留源文件哈希与原文行号：

```bash
python scripts/topic_tools.py build --root ./sources --files textbook.md paper.md --out ./work/topics
python scripts/topic_tools.py query ./work/topics --root ./sources --query "一级反应半衰期与初始浓度有关吗" --limit 5
python scripts/topic_tools.py read ./work/topics --root ./sources --id "<返回的块 ID>"
```

有 doc2md `agent/` 索引时增加 `--agent-index ./sources/agent` 复用分块与页码；没有页码映射时只给行号。中文同义表达可用 `--terms` 补充原文关键词。工具生成章节路由和候选片段，不自动生成摘要或科学结论；来源变化会拒绝查询，需重建并复核证据。助手按“主题入口、可选摘要、原文证据”升级阅读，数值、公式、条件和分歧回源核对。详见 [读取与实际问答验证规范](references/topic-reading.md)。

## 与解析工具衔接

可直接读取 [sanmu-doc2md](https://github.com/luffysolution-svg/sanmu-skills/tree/main/skills/sanmu-doc2md) 产物，但不依赖该技能。已有可靠解析结果优先复用；原生文字导出只作为阅读文本，复杂公式、图表及双栏顺序需要回到PDF核对。未获取SI时明确标注，不猜测其中的实验或计算参数。

Zotero只作为可选来源，不直接操作数据库，不遍历存储目录。读取与本地实测不修改文献库；写笔记或批注依用户授权执行并回读。

开发者可从源码仓库根目录运行 `python -m unittest discover -s tests/sanmu-paper-reader -v`（图像回归需 PyMuPDF 与 Pillow）。测试位于根目录，不随技能安装要求执行。SOP 是基于已读原文的复现准备草案：未知用量、升温程序、校准等需标缺项与出处，经实验人员审阅后再制定执行规程。

## 验证与限制

当前以材料与化学为主要迭代领域，不声称已覆盖所有子方向。

机械检查通过只表示文件与引文结构可核对，不证明页码、图号、语义支持或科学结论正确。解读需要区分作者报告、助手解释和待验证假设。个人文献库问答不等同完整系统综述。

## 许可证

[MIT](LICENSE)。文献PDF、用户文库与私人批注不随技能分发。
