# 渠道发布

**简体中文** · [English](README.en.md) · [返回合集](../README.md)

技能源码只维护在 `skills/`，各渠道的包都由 `build.py` 从同一份源码生成：

| 渠道 | 发布物 | 方式 |
| --- | --- | --- |
| Claude Code / Codex / ZCode | 仓库本身（`.claude-plugin/`、`.agents/plugins/`）及 `zcode/` 插件包 | 推送即生效，用户从仓库市场或 Release 安装 |
| npx skills / skills.sh / SkillsMP | 公开 GitHub 仓库 | 自动索引 |
| GitHub Release | 合集 ZIP + 各平台适配包 + `SHA256SUMS` | `gh release create` |
| 腾讯 SkillHub | `skillhub/<技能名>/` 目录 | `skillhub publish` |
| WorkBuddy | `workbuddy/<技能名>-<版本>.zip` | 开放平台网页上传 |

## 目录

- 版本与打包
- 仓库开发布局
- 构建产物
- Claude Code 与 Codex
- GitHub Release
- 腾讯 SkillHub
- WorkBuddy
- SkillsMP 与 skills.sh

## 版本与打包

版本号记录在 `manifest.json` 的 `version` 和 `workbuddyVersion`，同时更新根目录与 ZCode 外壳的 `package.json`、各宿主 `plugin.json`、带版本的市场清单，以及 CLI、服务端、面板内的版本标识。宿主测试从发布 manifest 读取版本，避免每次发布修改固定断言；`check` 校验各 manifest、市场和服务端/面板的一致性。ZCode 外壳文件必须完整且被 Git 跟踪，缺失时构建失败。技能名称保持不变；腾讯 `slug` 默认与技能同名，首次发布前确认没有被占用。

从仓库根目录执行，只需要 Python 3.10+ 和 Git：

```bash
python distribution/build.py check
python -m unittest discover -s distribution/tests -v
python distribution/build.py build --out dist/<版本>
```

几条规则和背后的原因：

- **只打包 Git 已跟踪的文件**。新加的运行文件要先 `git add` 再构建，否则会报缺失，而不是悄悄漏包。
- **技能包只收运行所需的文件**：根目录的 `SKILL.md`、README、LICENSE、requirements，以及 `scripts/`、`references/`、`assets/`。测试、样例、开发文档和 ROADMAP 留在仓库；嵌套的测试、缓存、`_selftest.py`、凭证或本地依赖混进运行目录时，检查直接失败。
- **不覆盖已有输出**。输出目录已存在会拒绝构建，重新验证请换一个新目录。
- **静态检查**：校验标准 Agent Skills frontmatter、宿主版本与导出资源，以及 `SKILL.md` 和 `references/` 里的本地 Markdown 链接、反引号中的 `references/*.md` 路径。链接目标必须存在且被打进同一个技能包；在线链接、代码块里的示例和 Obsidian 双链不检查。
- **体积预算**：`manifest.json` 的 `packageBudget` 是本项目自定的上限，合集运行文件不超过 200 个、解压后不超过 5 MiB。腾讯网页上传的硬限制是单包 200 个文件、10 MB，这里沿用同样的文件数，体积留出余量。`check` 打印合集和各技能的文件数与字节数，`build` 打印每个 ZIP 的文件数、解压前后大小。

## 仓库开发布局

运行源码在 `skills/<技能名>/`，开发资产集中在仓库根目录：

- `tests/sanmu-doc2md/`、`tests/sanmu-paper-reader/`、`tests/sanmu-paper-search/`：离线测试，doc2md 的入口是 `tests/sanmu-doc2md/_selftest.py`；
- `examples/sanmu-doc2md/`：原始样例、PDF、截图、`RESULTS.md` 和 `RESEARCH-VALIDATION.md`；
- `docs/development/sanmu-doc2md/ROADMAP.md`：后续开发计划；
- `docs/showcases/sanmu-paper-reader/images/`：reader 展示截图；
- `distribution/tests/`：打包回归测试。

```bash
python tests/sanmu-doc2md/_selftest.py
python -m unittest discover -s tests/sanmu-paper-reader -v
python -m unittest discover -s tests/sanmu-paper-search -v
node --test tests/zcode-paper-search/test_mcp_server.mjs tests/zcode-paper-search/test_panel.mjs
```

样例和展示图片只供浏览仓库和开发验证；安装后的技能里，运行时链接都应指向包内资源。

## 构建产物

`dist/<版本>/` 下：

| 文件 | 用途 |
| --- | --- |
| `sanmu-skills-<版本>.zip` | 完整合集：三技能、Claude/Codex 市场清单、Pi 扩展、DSH 完整工作台、CLI、MCP 与浏览器工作台 |
| `skillhub/<技能名>/` | 交给 `skillhub publish` 的目录 |
| `skillhub/<技能名>-<版本>.zip` | 与上面目录内容一致的归档，根部是 `SKILL.md` |
| `workbuddy/<技能名>-<版本>.zip` | WorkBuddy 技能包，根部是 `SKILL.md` |
| `zcode/sanmu-paper-search-zcode-plugin-<版本>.zip` | ZCode 桌面端「三木文献检索」侧栏插件包（由 `distribution/zcode/` 外壳与三技能源码自动同源组装） |
| `npm/sanmu-skills-<版本>.tgz` | Pi / DSH / npm 安装器接受的标准 `package/` tarball，与完整合集同源 |
| `SHA256SUMS` | 以上 8 个 ZIP 与 1 个 TGZ 的 SHA-256，LF 换行 |

**平台专用元数据只在构建时注入**，源码 `SKILL.md` 保持标准格式，正文和资源在各渠道完全一致：

- SkillHub：`slug`、`displayName`、`version`、`summary`、`license`；
- WorkBuddy：`display_name`、`display_name_en`、`description_zh`、`description_en`、`version`、`author`。

SkillHub 展示名写在 `manifest.json` 的 `displayName`；WorkBuddy 使用独立的 `workbuddyName` 标识和 `workbuddyDisplayName` / `workbuddyDisplayNameEn` 展示名。简介共用 `summary` / `summaryEn`，作者写在顶层 `author`。

两个平台都会拒绝没有扩展名的文件，所以 `skillhub/` 和 `workbuddy/` 包里不含 `LICENSE`；许可证信息由 frontmatter 和合集包提供。

## 完整插件与宿主验收

完整包以根 `package.json` 的 `files` 和构建白名单共同约束运行文件，包含全部三个技能。Claude 使用 `.mcp.json`，Codex 使用标准 `mcp.json` 及 `.codex-plugin/mcp-env.json` 环境变量覆盖；Pi 扩展调用原生 `registerMcpServer`。DSH 的 `cordis.patch.yml` 按包名注册：合集为 `sanmu-skills`，独立外壳为 `sanmu-paper-search`，使宿主能同时发现 Host、Client、图标和中英文显示信息。两个包分别导出 `./client`、`./icon`、`./locale/*.json`，并携带各自的 `scripts/dsh-client.js`、`locale/en.json`、`locale/zh.json`。构建保留两个客户端各自的模块 ID，独立外壳不被根客户端覆盖。

DSH 0.2.1-rc.3 支持 Electron 内置 Node.js 22.20.0，也支持普通 Node.js ≥22.20。安装后从对话头「三木文献检索」或右栏「+」进入完整工作台，直接复用随包的 ZCode 面板原文，保留全部 24 个 MCP 工具、文献详情、内联抽屉、API 设置、实时力导向引文图谱与年份时间轴；无需独立侧栏插件，旧 `dsh-plugin-sanmu-sidebar` 保持禁用。依赖装在私有数据目录，`doctor` 不执行安装。其他终端宿主可通过 `open_panel` 或 `sanmu panel` 使用浏览器工作台；使用实际返回的完整启动链接建立会话，精读指令通过复制回到对话。

```bash
node --test tests/harnesses/*.mjs
SANMU_TEST_HOSTS=1 python tests/harnesses/test_marketplace_hosts.py
```

宿主测试使用隔离配置，不改当前用户安装。

DSH 的原生 React 容器默认通过现有同源认证连接，GET Host 只读资源 `/sanmu-paper-search/workbench`，获取随包 `assets/panel.html` 的完整原文和版本，再以 `srcDoc` 文档复用原 DOM、CSS 和交互逻辑。`/sanmu` 的 `panel/read` 可作为回退或测试入口。受控 `postMessage` 将 `tools/call` 送入同一宿主策略桥接，将 `ui/message` 送入当前会话，并同步初始化信息、主题和语言环境。这个加载路径复用 DSH 现有连接，无需另建 HTTP 服务、端口、浏览器启动令牌或跨源 CORS 放行。

分发回归检查合集 ZIP、TGZ、ZCode ZIP 中客户端、模块 ID、图标和中英文元数据齐全，核对根包与独立包的面板内容和源码逐字节一致，保留力导向物理引擎与时间轴，并检查客户端无固定回环地址、启动令牌或 CORS 服务依赖。

Pi / DSH 官方运行时不存在时，相应集成测试会明确跳过；发布验收须单独提供已安装的官方运行时，并在真实桌面端确认 24 个 MCP 工具已注册，逐项检查对话头入口、右栏、完整面板、文献详情、力导向图谱、时间轴和工具操作。

通过通用插件市场安装不等于官方目录收录，npm tarball 不代表已发布 npm 包。

## Claude Code 与 Codex

```bash
claude plugin validate .claude-plugin/marketplace.json --strict
claude plugin validate .claude-plugin/plugin.json --strict
```

Codex 的仓库市场是 `.agents/plugins/marketplace.json`，以本地来源 `./` 指向根插件。可以在独立的测试配置里添加本地市场，再执行 `codex plugin add sanmu-skills@sanmu-skills` 验证。

自建 GitHub 市场不等于进入 Anthropic 或 OpenAI 的官方目录。OpenAI 公共目录需要另外通过[开发者平台提交](https://developers.openai.com/plugins/deploy/submission)，上传 ZIP、处理检查结果后等待审核。

## DeepSeek 自建市场

`deepseek/index.json` 遵守官方 format 1，Git 来源固定为实际含完整包的 40 位提交。先提交功能代码，再运行 `python distribution/deepseek/generate_index.py --commit <完整提交SHA>` 生成索引并提交；版本必须取该提交的 `package.json`。索引是发布元数据，不进入技能或 npm 运行包，避免自引用提交问题。

DSH 桌面端通过现有「插件 → 添加插件」输入 GitHub 仓库标识（`github:luffysolution-svg/sanmu-skills`）或完整 TGZ 下载地址安装。保留的 `desktop` profile 由桌面端管理；普通 CLI 仅用于其他已有 profile，例如 `dsh plugin --profile <已有的非desktop-profile名> add github:luffysolution-svg/sanmu-skills`。`deepseek/index.json` 作为 format 1 索引发布元数据。Pi 使用 `pi install` 原生包入口，`pi-package` 关键字为目录提供元数据，不能把提供关键字称为已被官方收录。

## GitHub Release

Release 页面上的文件是平铺的，没有 `skillhub/`、`workbuddy/` 子目录。WorkBuddy 包使用独立的 `-paid` 名称，上传时保留文件名，并生成一份与平铺文件名对应的校验文件：

```bash
V=<版本>; D=dist/$V; R=$D/release; mkdir $R
cp $D/sanmu-skills-$V.zip $D/skillhub/*.zip $D/workbuddy/*.zip $D/zcode/*.zip $D/npm/*.tgz $R/
(cd $R && sha256sum *.zip *.tgz > SHA256SUMS)
gh release create v$V $R/* --title "sanmu-skills v$V" --notes-file <更新说明.md>
```

## 腾讯 SkillHub

三个技能都已发布：

| 技能 | slug |
| --- | --- |
| sanmu-doc2md | `sanmu-doc2md` |
| sanmu-paper-reader | `sanmu-paper-reader` |
| sanmu-paper-search | `sanmu-paper-search` |

上传请用构建产物，不要用 `skills/<技能名>/` 源码目录：源码目录里的 `node_modules/`、`__pycache__/` 会让 doc2md 超过 200 个文件的上限，其余技能的 `.pyc` 会被跳过。后续版本在控制台“版本历史”中更新，slug 不变。

使用 API Token 发布前，按[官方教程](https://skillhub.cn/tutorials)完成实名认证和配置。官方的 Windows 教程使用 WSL。Token 不要写进源码、命令示例或提交记录。

```bash
skillhub auth whoami
skillhub publish ./dist/<版本>/skillhub/sanmu-doc2md --dry-run
skillhub publish ./dist/<版本>/skillhub/sanmu-paper-reader --dry-run
skillhub publish ./dist/<版本>/skillhub/sanmu-paper-search --dry-run
```

预检通过后，去掉 `--dry-run`、加上 `--changelog "<版本>：更新说明"`，逐个提交。返回 `pending_review` 只表示已提交，不等于已经公开上架，要以控制台显示为准。

## WorkBuddy

在 [WorkBuddy 开放平台](https://open.workbuddy.cn)左侧选 **发布管理 → 技能**，上传 `workbuddy/<技能名>-<版本>.zip`，三个技能分别上传。

- **不要选“专家”入口**。“专家”是一个 Agent 角色包，要求 `.codebuddy-plugin/plugin.json`、`agents/*.md`、512×512 头像和中英文展示文案，结构完全不同；把技能包传到那里会报“未找到 .codebuddy-plugin/plugin.json”。
- 技能包的必填字段按[官方技能文档](https://open.workbuddy.cn/docs/skill)：`description`、`description_zh`、`description_en`、`version`、`author`。展示名虽未标必填，但缺失时实际会解析失败，所以也一并写入。
- 付费资产名称只支持中文、英文字母、数字、`-` 和 `_`，不能含空格或间隔号。构建时校验中英文展示名；当前中文名为“三木学术文档转Markdown”“三木文献精读”“三木文献检索”。后台展示名从包内 `display_name` 解析且只读，改名后需重新上传 ZIP。
- 本次三个付费技能均从“创建”入口重新建立，WorkBuddy 标识为 `sanmu-doc2md-paid`、`sanmu-paper-reader-paid`、`sanmu-paper-search-paid`，原已发布记录保持下架。实测旧免费记录的“更新版本”页不提供付费选项，而创建时沿用旧标识会提示技能已存在；渠道独立标识避免冲突，源码及 SkillHub 标识保持不变。
- `workbuddyVersion` 与 `manifest.json` 的合集版本保持一致，构建检查会拒绝不一致的值。未设置时沿用合集版本。更新已有记录时，平台要求包内版本严格大于当前最新版本。
- 三个新技能统一选择“按月付费”，金额为 `3` 元/月。价格在后台设置，ZIP 不携带定价；保存后核对确认页的“按月付费·3元/月”，再提交审核。
- 解析失败时，先对照官方文档的“技能基础结构”排查；仍无法定位，可发邮件到 openworkbuddy@tencent.com。

## SkillsMP 与 skills.sh

GitHub topics 保留 `agent-skills`，并加上 SkillsMP FAQ 列出的 `claude-skills` 或 `claude-code-skill`。SkillsMP 会自动索引公开仓库，等它同步后到详情页实际核对，不要把 topic 设置成功当作已经收录。

skills.sh 的[合集页面](https://skills.sh/luffysolution-svg/sanmu-skills)通过 `npx skills add` 的安装和匿名遥测自动更新，不需要另外发布 npm 包。
