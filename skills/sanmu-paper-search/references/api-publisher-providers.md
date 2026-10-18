# 出版社全文获取路线

本技能只维护两条出版社 API 路线：Elsevier 和 Wiley。其他出版社不维护 API 适配或平台清单，统一使用浏览器下载。

| 平台 | API 路线 | 浏览器路线 |
| --- | --- | --- |
| Elsevier | Article Retrieval API，PDF | API 不可用时按通用页面提取主文 PDF |
| Wiley | TDM API，PDF | API 不可用时按通用页面提取主文 PDF |
| 其他出版社 | 不提供本技能接入的 API | 通用 PDF 链接提取；成功取决于用户会话和页面 |

## API 路线

Elsevier 和 Wiley 的申请入口、环境变量、权限边界、限流和错误处理见 [全文获取 API](api-access.md)。

两条路线都只处理 PDF：Elsevier 覆盖 `10.1016/`，Wiley 覆盖 `10.1002/`、`10.1111/`。持有 Key 不等于拥有目标文章权限；机构订阅内容通常要求本机处于校园网或相应授权网络。

## 浏览器路线

`webvpn_acquire.py fetch --backend browser` 复用用户已登录的持久 Chrome 配置：

1. 打开图书馆 WebVPN 或目标出版社页面。
2. 对 Elsevier/Wiley 执行一次图书馆数据库入口握手。
3. 其他出版社直接打开目标页面，使用通用 PDF 链接提取：`citation_pdf_url`、`pdfdirect`、`articlepdf`、`getPDF`、`.pdf` 等。
4. 按主机串行限速，默认 15±5 秒、单次每主机 20 篇、每日每主机 60 篇。
5. 验证码或机构登录墙出现时，`--keep-open` 暂停并等待用户完成，然后重试当前条目。

其他出版社没有专门的主机/DOI 映射和入口握手，因此“能打开页面”不等于“能取得 PDF”。没有稳定 PDF 链接的页面会保留 `needs_manual`，由用户决定下一步。

CARSI/Shibboleth 走出版社的“Access through your institution”页面，由用户完成机构身份认证；脚本只识别登录墙、暂停和续跑，不自动选择学校、填写账号或绕过验证码。

## 不做的事

- 不为未接入 API 的出版社维护逐平台 API 清单。
- 不把网页内部请求、元数据接口或 OA 端点写成订阅 PDF API。
- 不把 SI、XML 或其他全文格式冒充主文 PDF。
- 不通过脚本绕过登录、付费墙或验证码；下载仍受机构许可和合理使用约束。
