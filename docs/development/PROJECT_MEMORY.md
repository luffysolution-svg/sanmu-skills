# sanmu-skills 跨 Harness 开发与发布项目记忆主文档

> 本文档为 `sanmu-skills` 项目的技术架构、多宿主集成机制、发布标准和开发约束的**单一权威来源（Single Source of Truth for Project Memory）**。随 Git 仓库维护，供在任何 Harness（DeepSeek Harness、ZCode、Claude Code、Codex、Pi 等）中开发、维护或发布时随时查阅并同步更新。

---

## 一、项目全貌与定位

- **项目名称**：`sanmu-skills`（三木的 Agent Skills 合集，公开仓库 `https://github.com/luffysolution-svg/sanmu-skills.git`）。
- **核心组件**：
  1. **三项核心科研技能（`skills/`）**：
     - `sanmu-paper-search`：学术文献检索、DOI/稳定标识核验、引文网络扩展（参考文献、施引文献、相似推荐）、作者代表作追踪、Europe PMC 全文/补充材料（SI）抓取、多源语义补搜（SciVerse、AI4Scholar、easyScholar 分区、Serper、Exa）、开放获取 OA PDF 下载与本机 Zotero 双向联动。
     - `sanmu-doc2md`：学术文档转 Markdown 知识库，保留 LaTeX 规范公式、高清原图与来源页码，支持科研论文（主文+SI 双链）、课程课件（PPTX/PDF）与长篇教材，提供 MinerU 云端 OCR、`local_fast` 本地极速直提、KaTeX 浏览器渲染预览及结构/公式校验。
     - `sanmu-paper-reader`：文献深度图文精读、证据与推论边界审查、实验复现充分性评估与机理对照。
  2. **统一 MCP 服务与可移植 CLI（`scripts/`）**：
     - 24 个统一规范的学术与文档 MCP 工具（前缀 `mcp__sanmu-paper-search__` 或宿主命名空间），由 Node.js MCP 服务端（`scripts/mcp-server.mjs`）驱动。
     - 可移植 CLI `sanmu.mjs`（子命令 `mcp`、`search`、`doc2md`、`reader`、`panel`、`doctor`、`setup`）。
  3. **完整科研工作台（`assets/panel.html`）**：
     - 8600+ 行无外部构建依赖的完整 Web 工作台文档，包含检索发现、检索结果、Obsidian 级 60fps 物理力导向星系图谱、年份演进时间轴、文献收藏篮、历史批次管理、文档解析全流程控制及 9 大 API 密钥配置面板。
- **维护克隆约定**：
  - **主维护克隆**：`F:\个人知识库\.agents\repos\wusen-skills`（所有开发、测试、构建和发布操作在此执行）。
  - **用户级副本**：`C:\Users\Administrator\.agents\repos\wusen-skills`（发布后通过 `git merge --ff-only` 安全快进同步）。
  - **联接入口**：`F:\个人知识库\.agents\skills\` 与 `C:\Users\Administrator\.agents\skills\` 指向各克隆的 `skills/` 子目录，合集仓库置于发现目录之外，避免重复加载。

---

## 二、代码布局与单源架构原则

```
.
├── skills/                     # [单源] 三项技能标准源码（业务逻辑与 Python 核心）
│   ├── sanmu-doc2md/           # OCR、文档切片、LaTeX 校验与 KaTeX 预览
│   ├── sanmu-paper-reader/     # 文献图文对照、精读评估与证据提取
│   └── sanmu-paper-search/     # 多源检索、引文网络、OA/SI、Zotero 核心
├── scripts/                    # [单源] 共享 Node MCP 服务、可移植 CLI 与运行时
│   ├── sanmu.mjs               # 统一命令行入口
│   ├── mcp-server.mjs          # 24 项 MCP 工具标准服务端实现
│   ├── runtime.mjs             # Python 私有 venv、依赖自检与锁管理
│   ├── panel-server.mjs        # 独立浏览器工作台安全服务端（Origin/SameSite Strict）
│   ├── dsh-client.js           # DeepSeek Harness 原生客户端适配层
│   └── zotero-tools.mjs        # 本机 Zotero 8+ 桌面端 API 联动
├── assets/                     # [单源] 图标、Logo 与完整工作台页面（panel.html）
├── distribution/               # [分发] 各渠道外壳、构建脚本与发布测试
│   ├── manifest.json           # 项目发布与渠道元数据清单
│   ├── build.py                # 自动化组装、静态校验与多渠道打包引擎
│   ├── deepseek/               # DeepSeek 自建市场索引生成器与 index.json
│   ├── zcode/sanmu-paper-search/ # ZCode 独立插件外壳与 DSH 适配器
│   └── tests/                  # 打包回归与多平台归档一致性测试
├── tests/                      # 离线回归、各宿主集成测试与夹具
└── 各宿主描述文件               # plugin.json、mcp.json、cordis.patch.yml 等
```

### 单源原则（Single Source of Truth）
1. **技能业务逻辑仅存在于 `skills/`**：严禁在宿主外壳或发布目录下维护分叉的 Python 代码。
2. **工作台 UI 仅维护 `assets/panel.html`**：力导向图谱、时间轴、抽屉与各表单均在此单一文件中迭代；构建脚本自动将该文件分发给 ZCode 独立插件和合集包。
3. **平台专用元数据在构建时注入**：源码 `SKILL.md` 保持标准 Agent Skills 规范；SkillHub 的 `slug`、WorkBuddy 的 `workbuddyName`（`-paid` 后缀）与展示名由 `distribution/build.py` 按 `manifest.json` 自动处理。

---

## 三、各宿主技术架构与挂载机制

### 1. DeepSeek Harness (DSH) 桌面端
- **运行环境**：DSH `>=0.2.1-rc.3 <0.3.0`，Node.js `>=22.20.0`。
  - 桌面版实测捆绑：Electron `44.7.0` + Node `24.21.0`。
  - 关键适配：`resolveNodeInvocation` 必须将 Electron 进程环境变量设为 `ELECTRON_RUN_AS_NODE=1`，且包管理器的 `pnpm` 参数不能当成 Node 参数传给运行时。
- **宿主与客户端配对**：
  - Host 适配器：`distribution/zcode/sanmu-paper-search/scripts/dsh-plugin.mjs`。
  - Client 适配器：`scripts/dsh-client.js`（独立外壳中为 `distribution/zcode/sanmu-paper-search/scripts/dsh-client.js`，loader ID 对齐为 `sanmu-paper-search`）。
  - Cordis 补丁：`cordis.patch.yml` 必须使用**包名**（合集为 `sanmu-skills`，独立外壳为 `sanmu-paper-search`），不能写相对路径，以便 DSH 客户端发现 `./client` 导出与 locale 元数据。
- **1:1 原生 UI 还原架构**：
  - 原生 React 容器挂载于右侧栏 `sidebar.right.pane.tab`（Kind `sanmu-paper-search`）并在会话操作栏 `conversation.session.header.actions` 注册快捷按钮。
  - **不启动额外的 HTTP 监听器/端口**：Host 端通过 DSH 既有的 `webServer` 注册精确只读路由 `GET /sanmu-paper-search/workbench`，经过 DSH `connection.requestRejection` 强校验同源签名 Cookie，返回带安全 CSP 的完整 `panel.html` 原文及版本信息。
  - 原生容器使用 `iframe srcDoc` 加载该完整文档，完全保留 ZCode 相同的 DOM 结构、CSS 样式、力导向物理引擎、时间轴与全部交互。
- **受控消息桥接（Message Bridge）**：
  - `tools/call`：通过 Host 内部 `/sanmu` 命令管道调用，严格经过 Harness 的会话权限与工作区策略流水线，并动态绑定当前会话的 `workingDirectory`。
  - `ui/message`：通过 DSH `ctx.remote.session.prompt` 提交，直接进入当前活跃会话，避免在命令日志中堆积大文本。
  - **上下文消费防串染原则**：会话交接（sendToChat）在 dispatch 时立即消费当前 `modelContext` snapshot 并清空，失败不恢复全局缓存，防止前一篇论文的元数据残留至下一条无关消息。
  - **主题与字体同步**：从宿主 `document.body` 读取计算样式（`--dsw-alias-bg-base`、`--dsw-font-family`），通过 `theme/change` 在 `requestAnimationFrame` 之后同步至 iframe，确保深色/浅色与正文字体无缝跟随。
- **避坑红线**：
  - 曾安装的单文件旧插件 `dsh-plugin-sanmu-sidebar`（硬编码 55497 端口）必须在 profile 中保持禁用，严禁重复启用。

### 2. ZCode 桌面端
- **外壳配置**：根目录 `.zcode-plugin/plugin.json`。
- **挂载模式**：
  - `ui.surfaces` 注册 `ui://sanmu-paper-search/panel.html`，以原生 Webview 侧栏挂载。
  - **单一 Server 规范**：MCP Server 键名统一收敛为 `sanmu`，消除历史版本 `sanmu` 与 `sanmu-paper-search` 双重声明导致的万级 Token 重复占用。
- **Zotero 联动**：直连本机 `http://127.0.0.1:23119`，支持分类读取、条目存入、PDF 自动关联、一键撤销（undo）与桌面定位。
- **缓存保护原则**：用户当前工作的 ZCode 插件安装缓存属于受保护环境，发布或测试时**严禁擅自覆盖、清空或重建低于当前版本的旧缓存**。

### 3. Claude Code & OpenAI Codex
- **Claude Code**：`.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json` 与 `.mcp.json`，支持 `claude plugin marketplace add` 及直接加载。
- **Codex**：`.codex-plugin/plugin.json`、`.agents/plugins/marketplace.json`、`mcp.json` 与 `.codex-plugin/mcp-env.json`。
- **边界**：自建 Git 仓库市场不等于进入 Anthropic / OpenAI 官方公开目录，不作夸大宣称。

### 4. Pi 扩展 (Inflection)
- `package.json` 声明 `"pi": { "skills": ["./skills"], "extensions": ["./extensions/pi.ts"] }`。
- `extensions/pi.ts` 调用官方 `registerMcpServer` 挂载 24 工具，支持 `pi install`。

### 5. 腾讯 SkillHub & WorkBuddy
- **SkillHub**：三个独立单技能包（`sanmu-doc2md`、`sanmu-paper-reader`、`sanmu-paper-search`），版本与合集一致。必须排除 `LICENSE`（平台拒绝无扩展名文件），解压累计 ≤50 MiB，单包 ≤200 文件、≤10 MB。
- **WorkBuddy**：三个月付付费技能包（`sanmu-*-paid`），必须为合法标识符（不能含空格），frontmatter 注入中英文展示名，统一 3 元/月。

---

## 四、版本发布标准与严格工作流

发布新版本是一项严肃的工程操作，必须严格遵循以下步骤与约束，**不得跳步**：

### 1. 发布前准备与版本号对齐
所有涉及版本号的文件必须保持完全一致（当前版本以 `distribution/manifest.json` 为准）：
- `distribution/manifest.json` (`version` 与 `workbuddyVersion`)
- `package.json` 与 `distribution/zcode/sanmu-paper-search/package.json`
- `plugin.json`、`.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`.zcode-plugin/plugin.json`、`distribution/zcode/sanmu-paper-search/.claude-plugin/plugin.json`、`distribution/zcode/sanmu-paper-search/.zcode-plugin/plugin.json`
- `marketplace.json`、`.claude-plugin/marketplace.json`、`.agents/plugins/marketplace.json`
- `scripts/sanmu.mjs`、`scripts/mcp-server.mjs`、`scripts/panel-server.mjs`、`scripts/dsh-client.js`、`assets/panel.html` 及对应独立外壳副本
- 运行同步脚本确保外壳客户端与语言包完全同步：
  ```bash
  python dist/harness-repair-20261029/sync-native.py  # 或执行对应复制
  ```

### 2. Git 跟踪原则（严防静默漏包）
`distribution/build.py` 的核心设计机制是**只打包 Git 已跟踪（tracked / staged）的文件**。
- **任何新增的运行文件、语言包、脚本或资产，必须先执行 `git add`**。
- 未跟踪的文件不会被打包；如果属于必选运行时，构建校验会直接报错终止。

### 3. 静态检查与完整离线回归
在执行构建前，必须在主克隆根目录运行并通过全部检查：
```bash
# 1. 静态分发规范与元数据检查
python distribution/build.py check

# 2. 格式与换行一致性检查（保持原文件 LF / CRLF，不产生多余空白差异）
git -c core.whitespace=cr-at-eol diff --check
git -c core.whitespace=cr-at-eol diff --cached --check

# 3. 核心分发与跨宿主回归
python -B -m unittest discover -s distribution/tests -v
node --test tests/harnesses/*.mjs
node --test tests/zcode-paper-search/test_mcp_server.mjs tests/zcode-paper-search/test_panel.mjs

# 4. 技能级 Python 离线测试
python -m unittest discover -s tests/sanmu-paper-search -v
python -m unittest discover -s tests/sanmu-paper-reader -v
```

### 4. 正式构建打包
构建输出必须指定一个全新的、唯一的目录（构建器严禁覆盖已有输出目录）：
```bash
python distribution/build.py build --out dist/release-<version>-<date>
```
产出必须包含以下 **10 项标准发布资产**（8 个 ZIP + 1 个 TGZ + `SHA256SUMS`）：
1. `sanmu-skills-<version>.zip`（全合集完整包）
2. `sanmu-skills-<version>.tgz`（npm / Pi / DSH 规范 tarball）
3. `sanmu-paper-search-zcode-plugin-<version>.zip`（ZCode 独立完整插件包）
4. `sanmu-doc2md-<version>.zip`（SkillHub 专用包）
5. `sanmu-doc2md-paid-<version>.zip`（WorkBuddy 专用包）
6. `sanmu-paper-reader-<version>.zip`（SkillHub 专用包）
7. `sanmu-paper-reader-paid-<version>.zip`（WorkBuddy 专用包）
8. `sanmu-paper-search-<version>.zip`（SkillHub 专用包）
9. `sanmu-paper-search-paid-<version>.zip`（WorkBuddy 专用包）
10. `SHA256SUMS`（包含上述 9 个归档的平铺哈希清单，LF 换行）

### 5. 源码提交与 DeepSeek 市场索引两阶段固定
DeepSeek 市场索引（`distribution/deepseek/index.json`）遵守官方 Format 1，其 `commit` 字段必须精确指向包含完整包的真实 40 位 Git Commit SHA。为避免自引用死锁，必须采用**两阶段提交**：
```bash
# 第一阶段：提交所有功能源码与版本更新
git add --update
git add <所有新增的运行时与测试文件>
git commit -m "fix/feat: <发布说明>"

# 获取刚刚生成的源码 Commit SHA
SOURCE_COMMIT=$(git rev-parse HEAD)

# 第二阶段：以该 SHA 生成固定的市场索引并单独提交
python distribution/deepseek/generate_index.py --commit $SOURCE_COMMIT
git add distribution/deepseek/index.json
git commit -m "chore(dsh): pin v<version> marketplace to verified source"
```

### 6. 标签创建与 GitHub Release 发布
```bash
# 打版本标签（指向最终包含索引的提交）
git tag -a v<version> -m "sanmu-skills v<version> release"

# 原子推送 main 分支与 tag
git push --atomic origin main refs/tags/v<version>

# 平铺上传全部 10 项发布资产至 GitHub Release
gh release create v<version> <平铺资产目录>/* \
  --repo luffysolution-svg/sanmu-skills \
  --verify-tag \
  --title "v<version> · <标题>" \
  --notes-file <发布说明.md>
```

### 7. 发布后验证与副克隆同步
1. **重新下载远端资产校验**：使用 `gh release download` 将 GitHub 上的资产下载到临时目录，逐一比对 SHA-256，确保 CDN 传输无损且与 `SHA256SUMS` 100% 一致。
2. **远端 CI 检查**：核验 GitHub Actions（Ubuntu / Windows 离线测试与构建工作流）全部成功。
3. **副克隆同步**：进入 `C:\Users\Administrator\.agents\repos\wusen-skills`，执行 `git fetch origin && git merge --ff-only origin/main`，保持双克隆严格对齐。

---

## 五、开发维护约束与避坑红线

1. **绝对禁止污染知识库根目录**：
   - 严禁在 `F:\个人知识库\` 根目录下创建 `plugins/` 或 `dist/` 目录。
   - 所有临时构建、测试夹具、诊断日志必须收敛在仓库隐藏目录内（如 `dist/` 或 `.agents/repos/wusen-skills/dist/`）。
2. **严禁破坏用户现有可用环境**：
   - 用户已有的 ZCode 本地插件配置、凭证和运行数据属于受保护财产，任何调试或更新不得无故清空或破坏。
   - 不随意修改已保存的 API Key（SciVerse、AI4Scholar、MinerU 等），凭证脱敏展示与传输。
3. **绝不冒称未发生的发布状态**：
   - 生成了 npm `.tgz` 不等于在 npm 公共 Registry 上发布了包（npm 需独立账号发布）。
   - GitHub 仓库自建市场（`.claude-plugin/marketplace.json`）不等于进入 Anthropic / OpenAI 官方审核目录。
   - SkillHub CLI 上传返回 `pending_review` 不等于已上架生效。
4. **测试与真实执行分离**：
   - 单元测试与 mock 回归不代替真实桌面端验证；真实界面验证后须恢复用户的原主题与字体偏好。
   - 测试用本地 PDF 转换使用 `local_fast` 原生引擎，不得随意消耗用户的云端 MinerU Token。
5. **保持文档与实现的双向维护**：
   - 本记忆文档若有扩展或修改，由主维护克隆首发，并通过 Git 提交同步至远端与副克隆。各 Harness 的本地入口（`AGENTS.md`、`MEMORY.md`）应保持轻量索引，统一引用本文档。
