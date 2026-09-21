# phix 官网

上海民办平和学校 PHIX 维修社的官网与网页端（<https://phix.ing>）。

纯标准库的 Python `http.server`，没有框架、没有构建步骤：`server.py` 既托管静态页面，
又把 `/api/*` 透传给 phix 服务端，并自己提供 `/app/` 网页端所需的平台数据接口。

---

## 目录

```
server.py                 # 站点服务：静态托管 + /api 透传 + /app 数据接口 + 账号/SSO
core_e2e.py               # 请求信封（X25519 密封盒 + AES-256-GCM，X-Phix-Enc: 1）
webapp_data.py            # 平台抓取层：EduPage 课表 / ManageBac 作业 / 网易企业邮
webapp_managebac.py       # ManageBac 客户端（学生 cookie 登录）
webapp_mb_parse.py        # ManageBac 页面解析
webapp_mb_stream.py       # ManageBac 通知/讨论/考试/课程详情（能力感知适配层）
deploy.py                 # 幂等部署到生产机（SSH/SFTP），含远端自检

*.html                    # 页面：首页/关于/文档/下载/日志/服务与支持/登录/注册/个人中心
app/                      # 网页端 SPA（Pinghe Launcher 网页版）
admin/                    # 管理后台
products/                 # 产品页（心履 / PH-Launcher / 硬件）
static/                   # 各页样式与脚本
assets/                   # Logo、风景照等站点图片
local-bridge/             # 可选的本机直连服务（客户端凭据不出本机时用它代抓）

test_*.py                 # 各页与接口的测试（含真浏览器 CDP 测试）
*.md                      # 设计/交付/契约/构建说明
checksums.txt             # 安装包哈希清单（与下载页一致）
```

## 本地跑起来

```powershell
# 站点（8940）
python -X utf8 server.py --port 8940

# 依赖 phix 服务端（8931）才有 /api/*；网页端的课表/作业/邮件还需要账号
```

测试（需要站点在跑）：

```powershell
python -X utf8 test_phix_site.py        # 站点外壳
python -X utf8 test_download_page.py    # 下载页
python -X utf8 test_app_web.py          # 网页端
python -X utf8 test_app_cdp.py          # 真浏览器（Edge CDP）
```

## 部署

```powershell
python -X utf8 deploy.py            # 推到生产机并自检；systemd 单元 phix-site.service
python -X utf8 deploy.py --verify-only
```

`deploy.py` 刻意**不覆盖**生产机上的 `content.json`、`.site_secret`、`.phix_pubkey`、
`logs.json` 与 `media/logs/` —— 那些是每台机器自己的数据。

## 仓库里没有什么（有意排除）

这个仓库**只有源码**。下列内容**不在**这里，与心履（`xinlv-web` 等）的做法一致：

| 排除项 | 为什么 |
|---|---|
| `content.json` | CMS 文案与后台改动，是生产数据；`deploy.py` 也不覆盖它。结构与默认值见 `content.json.example` |
| `logs.json` · `feedback/` | 「PHIX 日志」与反馈表单的**真实用户提交内容** |
| `admin_audit.log` · `.legacy_users.json` | 后台操作审计、旧账号哈希 |
| `media/` | 安装包（约 760 MB，走 GitHub Releases 与站点分发）+ 用户上传的照片 |
| `.site_secret` · `.phix_pubkey` · `.service_key` | 每台机器自己生成的密钥 |

想自己跑一份，把 `content.json.example` 复制成 `content.json` 即可（缺它时页面会用
HTML 里 `{{cms:key|默认值}}` 的内联默认值，站点照常工作）。

## 相关仓库

- [`phix-server`](https://github.com/huaziqian40-bot/phix-server) —— phix 统一账号与端到端加密云同步服务端
- [`Pinghe-Launcher-Lite`](https://github.com/huaziqian40-bot/Pinghe-Launcher-Lite) —— 轻量版桌面客户端
- [`PH-Launcher`](https://github.com/XKRyan/PH-Launcher) —— 完整版桌面客户端（上游）
- 心履：`xinlv-web` / `xinlv-windows` / `xinlv-macos` / `xinlv-android`
