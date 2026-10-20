# 渠道发布

**简体中文** · [English](README.en.md) · [返回合集](../README.md)

技能源码只维护在 `skills/`。Claude Code、Codex 通过仓库市场安装合集；腾讯 SkillHub 使用三个独立适配包；SkillsMP、skills.sh 引用公开 GitHub。

## 版本与打包

版本记录在 `manifest.json`，同时更新根 `plugin.json` 与 `.claude-plugin/plugin.json` 的版本。技能名称保持不变；腾讯 `slug` 默认使用同名标识，首次发布前检查平台是否已被占用。

从仓库根目录执行，仅需要 Python 3.10+ 和 Git：

```bash
python distribution/build.py check
python -m unittest discover -s distribution/tests -v
python distribution/build.py build --out dist/1.2.0
```

新文件必须先加入 Git 暂存区再构建。构建只使用 Git 跟踪的当前文件，不包含未跟踪的凭证、私人材料、缓存或本地依赖。输出目录已存在时拒绝覆盖，复测请指定新目录。

输出包括：

- `sanmu-skills-1.2.0.zip`：通用插件包，包含三个技能、根 `plugin.json`、Claude 插件声明及许可证；也可作为 OpenAI 公共插件目录的待提交包，仍需平台检查与审核。
- `skillhub/<技能名>/`：可直接交给腾讯 CLI 的目录。
- `skillhub/<技能名>-1.2.0.zip`：与对应目录内容一致的归档，根部包含 `SKILL.md`。网页是否接收此格式以实际上传入口为准。
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

按[官方教程](https://skillhub.cn/tutorials)完成实名与 API Token 配置。Windows 发布教程使用 WSL；不要把 Token 写入源码、命令示例或提交记录。

```bash
skillhub auth whoami
skillhub publish ./dist/1.2.0/skillhub/sanmu-doc2md --dry-run
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-reader --dry-run
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-search --dry-run
```

预检通过后，分别提交三个目录：

```bash
skillhub publish ./dist/1.2.0/skillhub/sanmu-doc2md --changelog "首次发布"
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-reader --changelog "首次发布"
skillhub publish ./dist/1.2.0/skillhub/sanmu-paper-search --changelog "首次发布"
```

返回 `pending_review` 表示已提交，审核通过后才能声称已上架。如果某个 `slug` 已被占用，核对本人账号和平台条目后再调整打包元数据；不改通用技能名。

## SkillsMP 与 skills.sh

GitHub topics 保留 `agent-skills`，并添加 SkillsMP FAQ 列出的 `claude-skills` 或 `claude-code-skill`。SkillsMP 自动索引公开仓库，等待同步并实际核对详情页；不把 topic 设置成功当作已收录。

skills.sh 的[合集页面](https://skills.sh/luffysolution-svg/sanmu-skills)通过 `npx skills add` 安装与匿名遥测统计更新，不需要另外发布 npm 包。
