window.__ModuleLoader__.load({
  id: "sanmu-skills",
  factory: (require) => {
    const React = require("react");

    const TAB_ID = "sanmu-skills";
    const TAB_KIND = "sanmu-workbench";
    const DEFAULT_PANEL_URL = "http://127.0.0.1:55497/?token=[REDACTED]";

    function renderSanmuIcon(props) {
      const size = (props && Number(props.size)) || 16;
      return React.createElement("svg", {
        viewBox: "0 0 24 24",
        width: size,
        height: size,
        fill: "none",
        stroke: "currentColor",
        strokeWidth: "1.75",
        strokeLinecap: "round",
        strokeLinejoin: "round",
        className: (props && props.className) || undefined,
        "aria-hidden": "true",
        focusable: "false",
      }, [
        React.createElement("path", { key: "p1", d: "M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" }),
        React.createElement("path", { key: "p2", d: "M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" }),
        React.createElement("path", { key: "p3", d: "M12 7v14" }),
      ]);
    }

    function WorkbenchView() {
      const [reloadKey, setReloadKey] = React.useState(0);
      const [panelUrl, setPanelUrl] = React.useState(() => {
        try {
          return localStorage.getItem("sanmu_workbench_url") || DEFAULT_PANEL_URL;
        } catch {
          return DEFAULT_PANEL_URL;
        }
      });
      const [status, setStatus] = React.useState("checking");
      const iframeRef = React.useRef(null);

      React.useEffect(() => {
        let active = true;
        fetch(panelUrl, { method: "HEAD", mode: "no-cors" })
          .then(() => {
            if (active) setStatus("connected");
          })
          .catch(() => {
            if (active) setStatus("idle");
          });
        return () => { active = false; };
      }, [panelUrl, reloadKey]);

      const handleRefresh = () => {
        setReloadKey((prev) => prev + 1);
        if (iframeRef.current) {
          try {
            iframeRef.current.src = panelUrl;
          } catch {
            // ignore
          }
        }
      };

      const handleOpenExternal = () => {
        try {
          window.open(panelUrl, "_blank", "noopener,noreferrer");
        } catch (err) {
          console.error("[sanmu-skills] failed to open external window:", err);
        }
      };

      return React.createElement("div", {
        style: {
          display: "flex",
          flexDirection: "column",
          width: "100%",
          height: "100%",
          backgroundColor: "var(--dsh-bg, #0d1117)",
          color: "var(--dsh-fg, #c9d1d9)",
          fontFamily: "inherit",
          overflow: "hidden",
        },
      }, [
        // 顶部工具栏
        React.createElement("div", {
          key: "toolbar",
          style: {
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 12px",
            borderBottom: "1px solid var(--dsh-border, #30363d)",
            backgroundColor: "var(--dsh-bg-subtle, #161b22)",
            fontSize: "12px",
            userSelect: "none",
          },
        }, [
          React.createElement("div", {
            key: "left",
            style: { display: "flex", alignItems: "center", gap: "8px", fontWeight: "600" },
          }, [
            React.createElement("span", {
              key: "dot",
              style: {
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: status === "connected" ? "#2ea043" : "#e3b341",
                display: "inline-block",
              },
              title: status === "connected" ? "服务在线" : "正在等待服务",
            }),
            React.createElement("span", { key: "title" }, "三木文献检索工作台"),
          ]),
          React.createElement("div", {
            key: "right",
            style: { display: "flex", alignItems: "center", gap: "6px" },
          }, [
            React.createElement("button", {
              key: "btn-refresh",
              onClick: handleRefresh,
              style: {
                background: "transparent",
                border: "1px solid var(--dsh-border, #30363d)",
                borderRadius: "4px",
                color: "inherit",
                cursor: "pointer",
                padding: "2px 8px",
                fontSize: "11px",
              },
              title: "重新加载面板",
            }, "刷新"),
            React.createElement("button", {
              key: "btn-open",
              onClick: handleOpenExternal,
              style: {
                background: "var(--dsh-accent, #238636)",
                border: "none",
                borderRadius: "4px",
                color: "#ffffff",
                cursor: "pointer",
                padding: "2px 8px",
                fontSize: "11px",
                fontWeight: "500",
              },
              title: "在系统独立浏览器中打开工作台",
            }, "浏览器打开"),
          ]),
        ]),
        // iframe 主体
        React.createElement("iframe", {
          key: `frame-${reloadKey}`,
          ref: iframeRef,
          src: panelUrl,
          title: "三木文献检索工作台",
          style: {
            flex: 1,
            width: "100%",
            height: "100%",
            border: "none",
            backgroundColor: "transparent",
          },
        }),
      ]);
    }

    return {
      inject: ["slots"],
      apply(ctx) {
        let disposeTabs = null;
        let disposeTabBody = null;
        let disposeKindBody = null;

        const registerTab = () => {
          const tabs = ctx.get("sidebarRightTabs") || ctx.sidebarRightTabs;
          if (!tabs || typeof tabs.register !== "function") {
            setTimeout(registerTab, 200);
            return;
          }

          try {
            disposeTabs = tabs.register({
              id: TAB_ID,
              kind: TAB_KIND,
              keepMounted: true,
              title: () => "三木文献检索",
              guide: [{
                id: "workbench",
                order: 85,
                title: () => "三木文献检索",
                description: () => "文献检索、关系图谱与文档解析工作台",
                icon: renderSanmuIcon,
              }],
            });

            const renderComponent = () => React.createElement(WorkbenchView);

            disposeTabBody = ctx.slots.register(
              { name: "sidebar.right.pane.tab", key: TAB_ID },
              renderComponent
            );
            disposeKindBody = ctx.slots.register(
              { name: "sidebar.right.pane.tab", key: TAB_KIND },
              renderComponent
            );
          } catch (err) {
            console.error("[sanmu-skills] failed to register sidebar tab:", err);
          }
        };

        registerTab();

        return () => {
          try { if (disposeTabs) disposeTabs(); } catch {}
          try { if (disposeTabBody) disposeTabBody(); } catch {}
          try { if (disposeKindBody) disposeKindBody(); } catch {}
        };
      },
    };
  },
});
