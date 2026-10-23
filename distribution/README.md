# 渠道发布

**简体中文** · [English](README.en.md) · [返回合集](../README.md)

技能源码只维护在 `skills/`。Claude Code、Codex 通过仓库市场安装合集；腾讯 SkillHub 使用三个独立适配包；SkillsMP、skills.sh 引用公开 GitHub。

## 版本与打包

版本记录在 `manifest.json`，同时更新根 `plugin.json` 与 `.claude-plugin/plugin.json` 的版本。技能名称保持不变；腾讯 `slug` 默认使用同名标识，首次发布前检查平台是否已被占用。

从仓库根目录执行，仅需要 Python 3.10+ 和 Git：

```bash
python distribution/build.py check
python -m unittest discover -s distribution/tests -v
python distribution/build.py build --out dist/1.5.0
```

新运行文件必须先加入 Git 暂存区再构建。构建以 Git 索引中的跟踪路径读取当前文件；发布前须暂存迁移，未暂存的运行文件缺失会报错，防止静默漏包。技能包仅接收根部 `SKILL.md`、README、LICENSE、requirements，以及 `scripts/`、`references/`、`assets/`。测试、样例、开发文档与 ROADMAP 不打包；嵌套测试、缓存、`_selftest.py`、凭证及本地依赖进入运行输入时检查失败。输出目录已存在时拒绝覆盖，复测请指定新目录。

静态检查校验标准 Agent Skills frontmatter、三个公开版本的一致性，以及 `SKILL.md` 和运行 `references/` 内的本地 Markdown 链接与反引号 `references/*.md` 路径。目标必须存在并进入同一个独立技能包；在线链接、围栏样例和 Obsidian 双链不作为本地文件检查。

`manifest.json` 的 `packageBudget` 是本项目自定预算：合集运行文件不超过 200 个，解压后总大小不超过 5 MiB，超出则失败。腾讯网页上传入口的硬限制是单包 200 个文件、10 MB，这里沿用同一文件数并取更小的体积余量；`check` 输出合集及各技能的文件数/原始字节数，`build` 输出每个 ZIP 的文件数、解压后与压缩后字节数。摘要只打印到终端，不生成质检报告。

## 仓库开发布局

运行源码在 `skills/<技能名>/`；开发资产集中在仓库根目录：

- `tests/sanmu-doc2md/`、`tests/sanmu-paper-reader/`、`tests/sanmu-paper-search/`：离线测试；doc2md 入口为 `tests/sanmu-doc2md/_selftest.py`。
- `examples/sanmu-doc2md/`：原样例、原始 PDF、截图、`RESULTS.md` 和历史 `RESEARCH-VALIDATION.md`。
- `docs/development/sanmu-doc2md/ROADMAP.md`：后续开发计划。
- `docs/showcases/sanmu-paper-reader/images/`：reader 展示截图。
- `distribution/tests/`：分发构建回归测试。

```bash
python tests/sanmu-doc2md/_selftest.py
python -m unittest discover -s tests/sanmu-paper-reader -v
python -m unittest discover -s tests/sanmu-paper-search -v
```

以上路径均相对于仓库根目录。样例与展示图片只供仓库浏览和开发验证，安装后的运行链接应指向包内资源。

输出包括：

- `sanmu-skills-1.5.0.zip`：通用插件包，包含三个技能、根 `plugin.json`、Claude 插件声明及许可证；也可作为 OpenAI 公共插件目录的待提交包，仍需平台检查与审核。
- `skillhub/<技能名>/`：可直接交给腾讯 CLI 的目录。
- `skillhub/<技能名>-1.5.0.zip`：与对应目录内容一致的归档，根部包含 `SKILL.md`。网页是否接收此格式以实际上传入口为准。
- `SHA256SUMS`：四个 ZIP 的 SHA-256 校验值。

腾讯包在原 `SKILL.md` 头部追加 `slug`、`displayName`、`version`、`summary`、`license`；通用源码不添加腾讯顶层专用字段。正文及资源保持一致。

## Claude Code 与 Codex

```bash
claude plugin validate .claude-plugin/marketplace.json --strict
claude plugin validate .claude-plugin/plugin.json --strict
```

Codex 的仓库目录为 `.agents/plugins/marketplace.json`，通过本地来源 `./` 指向根插件。可在独立测试配置中添加本地市场并执行 `codex plugin add sanmu-skills@sanmu-skills`。用户安装命令见合集首页。

自建 GitHub marketplace 不等于进入 Anthropic 或 OpenAI 官方目录。OpenAI 公共目录另需通过[开发者平台提交](https://developers.openai.com/plugins/deploy/submission)，上传 ZIP、处理检查结果并提交审核。

## 腾讯 SkillHub

三个技能已发布：

| 技能 | slug |
| --- | --- |
| sanmu-doc2md | `sanmu-doc2md` |
| sanmu-paper-reader | `sanmu-paper-reader` |
| sanmu-paper-search | `sanmu-paper-search` |

上传选构建产物 `skillhub/<技能名>-<版本>.zip`，不要选 `skills/<技能名>/` 源码目录：其中的 `node_modules/`、`__pycache__/` 会让 doc2md 超过平台 200 个文件上限，其余技能会因 `.pyc` 被跳过。平台限制为单包 200 个文件、10 MB；各版本文件数与大小以本次 `build` 输出为准。后续版本在控制台「版本历史」更新，slug 不变。

使用 API Token 发布时，按[官方教程](https://skillhub.cn/tutorials)完成实名与配置。Windows 发布教程使用 WSL；不要把 Token 写入源码、命令示例或提交记录。

```bash
skillhub auth whoami
skillhub publish ./dist/1.5.0/skillhub/sanmu-doc2md --dry-run
skillhub publish ./dist/1.5.0/skillhub/sanmu-paper-reader --dry-run
skillhub publish ./dist/1.5.0/skillhub/sanmu-paper-search --dry-run
```

预检通过后，分别提交三个目录：

```bash
skillhub publish ./dist/1.5.0/skillhub/sanmu-doc2md --changelog "1.5.0：修复 S2 检索与限速，新增可选 Exa 补搜"
skillhub publish ./dist/1.5.0/skillhub/sanmu-paper-reader --changelog "1.5.0：修复 S2 检索与限速，新增可选 Exa 补搜"
skillhub publish ./dist/1.5.0/skillhub/sanmu-paper-search --changelog "1.5.0：修复 S2 检索与限速，新增可选 Exa 补搜"
```

返回 `pending_review` 表示已提交，不等同于已经公开上架。

## SkillsMP 与 skills.sh

GitHub topics 保留 `agent-skills`，并添加 SkillsMP FAQ 列出的 `claude-skills` 或 `claude-code-skill`。SkillsMP 自动索引公开仓库，等待同步并实际核对详情页；不把 topic 设置成功当作已收录。

skills.sh 的[合集页面](https://skills.sh/luffysolution-svg/sanmu-skills)通过 `npx skills add` 安装与匿名遥测统计更新，不需要另外发布 npm 包。
