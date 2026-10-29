# sanmu-skills 开发者与 Agent 工作指引

本项目是 `sanmu-skills`（三木科研技能与工作台合集），包含三项核心科研技能（`sanmu-paper-search`、`sanmu-doc2md`、`sanmu-paper-reader`）、24 个统一 MCP 工具、可移植 CLI 及跨宿主工作台（支持 DeepSeek Harness、ZCode、Claude Code、Codex、Pi、SkillHub、WorkBuddy）。

无论在哪个 Harness（DeepSeek Harness、ZCode、Claude Code、Codex 等）中开发或维护本项目，必须遵守以下核心约束与发布标准：

---

## 1. 权威记忆与完整技术文档

- **技术全貌与发布手册**：详细架构、各宿主挂载机制、完整发布清单与历史排错见 [`docs/development/PROJECT_MEMORY.md`](docs/development/PROJECT_MEMORY.md)。
- **开发与发布前必读**：修改核心逻辑或发布新版本前，务必查阅 `PROJECT_MEMORY.md`，它是本项目唯一的跨平台权威记忆。

---

## 2. 核心架构与单源原则 (Single Source of Truth)

- **技能源码单源**：业务逻辑与 Python 核心只维护在 `skills/`，严禁在宿主外壳中分叉实现。
- **工作台单源**：完整前端界面只维护在 `assets/panel.html`（8600+ 行无依赖纯 HTML/JS，含力导向图谱、时间轴、批次管理等）；构建时自动同步分发。
- **DeepSeek Harness 原生 1:1 对齐机制**：
  - Host 适配器：`distribution/zcode/sanmu-paper-search/scripts/dsh-plugin.mjs`。
  - Client 适配器：`scripts/dsh-client.js`（独立外壳为 `distribution/zcode/sanmu-paper-search/scripts/dsh-client.js`）。
  - 通过 Host 只读路由 `/sanmu-paper-search/workbench`（带签名 Cookie 校验）提供 HTML，由原生 React 容器以 `iframe srcDoc` 呈现，不启动额外 HTTP 服务/端口。
  - 工具调用走 `/sanmu` 经过会话策略流水线并绑定工作区；会话消息通过受控 `postMessage` 提交到当前会话；主题与字体从 `document.body` 自动同步。
  - 必须保持旧的 `dsh-plugin-sanmu-sidebar` 处于禁用状态。
- **ZCode 挂载机制**：通过 `.zcode-plugin/plugin.json` 挂载 `ui://sanmu-paper-search/panel.html`，MCP Server 键名严格保持单一 `sanmu`。用户现有 ZCode 插件缓存与配置严禁破坏。

---

## 3. 日常开发与测试命令

所有常规测试在仓库根目录下运行（无需启动 GUI）：

```bash
# 1. 静态分发与元数据检查
python distribution/build.py check

# 2. 格式与空白检查（严格保持原有换行风格，不产生无关 diff）
git -c core.whitespace=cr-at-eol diff --check

# 3. 跨宿主与打包回归测试
python -B -m unittest discover -s distribution/tests -v
node --test tests/harnesses/*.mjs
node --test tests/zcode-paper-search/test_mcp_server.mjs tests/zcode-paper-search/test_panel.mjs

# 4. 技能 Python 离线测试
python -m unittest discover -s tests/sanmu-paper-search -v
python -m unittest discover -s tests/sanmu-paper-reader -v
```

---

## 4. 版本发布严格工作流

1. **版本号对齐**：更新 `distribution/manifest.json`、根/外壳 `package.json`、各宿主 `plugin.json`、`marketplace.json`、CLI 及工作台版本标识。
2. **Git 跟踪要求**：构建工具**只打包 Git 已跟踪（tracked / staged）的文件**。所有新增文件必须先 `git add`！
3. **正式构建**：
   ```bash
   python distribution/build.py build --out dist/release-<version>-<date>
   ```
   确认产出完整的 10 项发布资产（8 个 ZIP + 1 个 TGZ + `SHA256SUMS`）。
4. **两阶段提交（固定 DeepSeek 市场索引）**：
   - 第一阶段：`git commit -m "fix/feat: ..."` 提交源码。
   - 第二阶段：运行 `python distribution/deepseek/generate_index.py --commit <刚刚的源码SHA>` 生成索引并单独提交 `chore(dsh): pin v<version> marketplace to verified source`。
5. **打标签与推送**：
   ```bash
   git tag -a v<version> -m "sanmu-skills v<version> release"
   git push --atomic origin main refs/tags/v<version>
   ```
6. **GitHub Release 发布**：平铺上传 10 项资产并附带详细 Release Notes。
7. **同步副克隆**：进入 `C:\Users\Administrator\.agents\repos\wusen-skills`，执行 `git merge --ff-only origin/main` 保持双仓同步。

---

## 5. 红线约束

- **禁止污染知识库根目录**：严禁在 `F:\个人知识库\` 根目录创建 `plugins/` 或 `dist/`。
- **保护用户现有配置**：调试与测试绝不擅自清空或降级用户本地的 ZCode 插件缓存、用户配置或 API 凭证。
- **客观陈述发布状态**：未发布的 npm 包、未进入的官方审核市场不作虚假声称。
