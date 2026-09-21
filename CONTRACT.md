# phix 官网 & 全端改版 · 施工契约（所有子代理必读，改前先读）

> 这份文件是**唯一契约**。任何子代理不得改动不属于自己的目录；
> 需要跨目录的东西，一律通过本文件里定义的接口/约定来对接。
> 更新：2026-09-12。

---

## 0. 目录归属（硬约束，违反即事故）

| 目录 | 归属 | 内容 |
|---|---|---|
| `D:\phix\website\`（除 `app/`） | **站点代理**（kimi-k3） | Django 站点：营销页、账号中心、cookie 会话、对 phix 的加密代理 |
| `D:\phix\website\app\` + `D:\phix\website\static\app\` | **PHL 网页版代理**（kimi-k3） | PHL Web SPA（日程/edupage/managebac/邮箱），自包含静态包 |
| `D:\phix\server\` | **只读**（本轮谁都别改） | phix 服务端已够用，见 §4 |
| `D:\moodsite\` | **心履代理**（kimi-k3） | 心履网页端 + Flutter 三端客户端 |
| `D:\phl-dev\PH-Launcher\` | **PHL 代理**（kimi-k3） | Electron 客户端 |
| `D:\phl-lite-dev\` | **PLL 代理**（kimi-k3） | PLL 客户端 |
| `D:\phix\`（文档/脚本） | 主代理 | 契约、验收脚本、汇总 |

**铁律**（沿用全局约定）：绝不删除任何 `data/`；`D:\backup` 只进不出；不动 `192.168.5.35`；
不打印/不落盘任何口令、令牌、密钥；三个业务仓库**不 commit / 不 push**。

---

## 1. 设计系统（淡紫）

色板取自 logo 与首页照片（黄昏紫粉天空）：

```css
--phix-violet:  #8B2BE2;   /* logo 主紫 */
--phix-violet-2:#6D28D9;   /* 深紫（按钮 hover / 标题） */
--phix-lilac:   #C4B5FD;   /* 淡紫（描边 / 次级背景） */
--phix-mist:    #F5F3FF;   /* 极淡紫（页面底） */
--phix-dusk:    #E9D5FF;   /* 黄昏粉紫（hero 渐变上沿） */
--phix-rose:    #F0ABFC;   /* 照片里的粉（点缀 / 渐变） */
--ink:          #1E1B2E;   /* 正文 */
--ink-2:        #5B5670;   /* 次级文字 */
--paper:        #FFFFFF;
```

- 字体：系统栈 `"PingFang SC","Microsoft YaHei",-apple-system,"Segoe UI",sans-serif`；
  标题可用 `font-weight:800` + 字距 `-0.02em`。
- 圆角：卡片 16px、按钮 10px、输入 10px；阴影 `0 8px 24px rgba(109,40,217,.10)`。
- 风格参考 awwards：**大留白、超大标题、hero 全幅照片 + 渐变遮罩、滚动渐显**。
  但**不要**引入任何外部 CDN 依赖（内网/离线可用），字体图标一律内联 SVG。
- 暗色模式：不做（本轮）。

素材（已就位，直接用相对路径）：
- `assets/logo-transparent.png` —— 紫齿轮+K 的 PHIX 标（透明底，全站使用）。**顶栏高度 34px**，点击回首页。原始 `logo.png`（白底）已弃用但保留不删。
- `assets/hero-campus.jpg` —— 黄昏校园竖图。首页 hero 用 `object-fit:cover; object-position:center 30%`，
  上叠 `linear-gradient(180deg, rgba(30,27,46,.15), rgba(139,43,226,.55))`。

---

## 2. 站点结构（导航与页面）

顶栏（sticky，白底 + 底部 1px `--phix-lilac`）从左到右：

```
[logo→/]  关于我们  产品▾  文档        …右侧…  服务与支持  下载  [登录/注册] 或 [头像▾]
                        └ 弹出菜单：硬件（占位，灰，标"敬请期待"） / 软件：心履、PHL
```

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 首页 | hero（照片）+ 三个产品卡（心履/PHL/PHL Lite）+ 一句社团 slogan + 页脚 |
| `/about/` | 关于我们 | 社团介绍（文案自拟，**不得含任何真实个人信息**） |
| `/products/xinlv/` | 心履介绍 | 功能亮点 + 截图位（无图就用 CSS 插画占位）+ 「打开网页版」「下载客户端」按钮 |
| `/products/phl/` | PHL 介绍 | 同上 + 「打开网页版」按钮（→ `/app/`） |
| `/products/hardware/` | 硬件 | 占位页：一句"敬请期待" |
| `/docs/` | 文档 | 两篇技术文：心履架构、PHL/PHIX 加密与同步架构（**内容取自 `D:\phix\phix-协议规范.md` 的公开部分**，不得泄露密钥/令牌细节） |
| `/support/` | 服务与支持 | FAQ + 反馈表单（表单只写本地文件 `feedback/`，不发外部网络） |
| `/download/` | 下载 | 三个产品的安装包列表；**文件从各仓库 build 产物软链/拷贝**，见 §6 |
| `/account/` | 个人中心 | 见 §5 |
| `/app/` | PHL 网页版 | SPA，见 §5.2 |
| `/login/` `/register/` | 登录/注册 | 表单页；成功后回跳 `?next=` |

页脚：社团名 + 版权行 + 三个快速链接。**不出现任何真实姓名/学号/邮箱**。

---

## 3. 会话与 Cookie（站点代理实现，PHL 网页版只读）

- 登录：站点后端 `POST /auth/login/`（表单或 JSON）→ 后端拿用户凭据调 phix
  `POST {PHIX_SERVER}/api/v1/auth/login`（**走应用层加密信封**，复用 `D:\moodsite\web\core\phix_e2e.py`
  的 `seal_box/make_envelope/open_envelope_response`，把该文件**复制**进 `website/core/`，不要 import 心履）。
- 成功后写两个 **httpOnly + SameSite=Lax** cookie：
  - `phix_access`（15 分钟 JWT）
  - `phix_refresh`（30 天）
- 之后所有对 phix 的调用走站点后端代理 `POST /proxy/<path>/`：后端从 cookie 取令牌，
  令牌过期（`token_expired`）自动用 refresh 续一次再重试（**只重试一次**），续期成功回写 cookie。
- `GET /me/` 返回 `{username, avatar, has_session}`（未登录 401）。头像来自同步对象 `profile`（§4）。
- 登出 `POST /auth/logout/`：调 phix 注销 + 清 cookie。
- **PHL 网页版 SPA 不直接碰 phix**：它只调本站 `/proxy/...` 与 `/me/`，cookie 自动带上。

## 4. phix 同步对象（已存在，无需改服务端）

| 对象名 | 内容 | 谁写 |
|---|---|---|
| `settings.accounts` | 四平台凭据（edupage/managebac/邮箱/…），**已端到端加密** | 客户端；个人中心只读+改 |
| `schedule` | 日程 | 客户端 / PHL 网页版（可加事件） |
| `school` | 学校数据（edupage/managebac 抓取缓存） | 客户端；PHL 网页版只读 |
| `profile` | **新增约定**：`{"display_name":str,"avatar":"data:image/...;base64,....","updated_at":iso}` | 任一端；avatar ≤ 200KB（上传前客户端/网页端压到 256×256） |
| `mood` | **新增约定**：`{"entries":[{"id","ts","text","intensity"}]}` 心情记录 | 个人中心 / 心履 |

> `profile`、`mood` 只是普通同步对象名（符合对象名正则），服务端零改动。
> 各客户端把这两个名字加进自己的同步对象清单即可（见各端代理任务）。

## 5. 个人中心 `/account/`（站点代理）与 PHL 网页版 `/app/`

### 5.1 个人中心四个板块（tab）
1. **个人信息**：用户名（只读展示 + 说明"用户名不可改"）、**头像**（上传→压缩→写 `profile`）、
   **改密码**（调 phix `/auth/password`，需旧密码 + DEK 证明；网页端拿不到 DEK 时**降级**为
   "请到客户端修改"的提示，不要伪造）。
2. **心情记录**：读/写 `mood` 对象（列表 + 新增 + 删除）。
3. **日程**：读 `schedule`，可加/删事件（与客户端同格式：`{id,day,time,title,note,created}`）。
4. **密码管理**：读写 `settings.accounts`。其真实结构是**两层字典**：
   `{"<section>": {"username":…, "password":…, 可能还有 "subdomain"/"base_url"/"email"}}`，
   section 形如 `edupage` / `managebac` / `mail:某人@xx`（见 `D:\phl-lite-dev\hellopinghe\secrets.py`）。
   页面按 section 分组列出 `username`/`password`（默认打码），可改可加可删该 section 的字段；
   **明文只在浏览器内存**，提交即经 `/proxy/` 加密上传。

### 5.2 PHL 网页版 `/app/`（PHL 网页版代理）
- 自包含静态包：`website/app/index.html` + `website/static/app/*`。**不用构建工具**（vanilla JS + 一个 css）。
- 四个视图（左侧 tab）：**日程 / Edupage / ManageBac / 邮箱**。
  - 日程：读 `schedule`，可增删。
  - Edupage / ManageBac：读 `school` 里对应段，只读展示（课表/作业列表）。
  - 邮箱：**不做真实收发**；展示 `settings.accounts` 里邮箱账号 + 一个"去客户端收发"的引导。
- **不做**：AI 功能、本地文件同步、设置页。
- 未登录 → 跳 `/login/?next=/app/`。
- 数据全部经 `/proxy/sync/objects/<name>/` 读写；冲突策略：**网页版只追加，不删除别人写的条目**
  （日程删除只删自己 created 的）。

## 6. 下载页 `/download/`

列表项（文件名约定，构建代理产出后拷到 `website/media/downloads/`）：
`xinlv-windows.zip` `xinlv-macos.zip` `xinlv-android.apk`
`phl-windows-setup.exe` `phl-macos.zip`
`phllite-windows-setup.msi` `phllite-macos.dmg`
页面只列**存在**的文件（后端扫目录），缺的就灰掉标"构建中"。

## 7. 三端引导流程（心履 / PHL / PLL 共用规范）

启动后**第一步**问：「你有 phix 账号吗？」
- **有** → 登录框（服务器地址预填 `http://192.168.5.41:8931`）→ 成功后**拉一次云同步**，再进常规引导；
- **没有** → 推荐注册（一键跳注册）；**也允许"跳过"**；
- 注册成功或跳过后 → 进入**原有**的逐项配置引导（各平台密码等），配置完写本地并（若已登录）推一次同步。
- 引导期间任何一步失败都**不阻塞**：给"跳过/稍后再说"。
- 头像：登录后从 `profile` 拉；个人中心/设置里可改并推上去。

## 8. 验收（交付前，主代理统一跑）

1. **功能全链路**（kimi-k3 / qwen3.8-max）：心履 Win/Mac/Android 代码走查 + 网页端、PHL、PLL、
   官网、PHL 网页版 的 Windows + Web 实跑（登录→同步→改头像→日程增删→密码管理读写→登出）。
2. **UI 检查**（glm-5.3-flash / deepseek-flash）：只对 **Windows 客户端 + 网页** 截图比对 §1/§2。
3. **Mac/Android 代码审查**（kimi-k3）：不跑构建，只审代码路径是否会崩。
4. 回归：`D:\phix\server\devtools\run_all.py` 必须仍 **710/710**；PHL `npm test`、PLL `_ui_test.py`、
   心履 `manage.py test core` 全绿。

---

## 9. 官网技术选型（硬约束，两个站点代理都必须遵守）

- **不用 Django / Flask / 任何新依赖**。站点 = 一个 **stdlib `http.server`（ThreadingHTTPServer）**
  的 `D:\phix\website\server.py`，用 **`D:\phix\server\.venv\Scripts\python.exe`** 运行
  （该 venv 里有 `cryptography`，信封加密要用；`requests` 也有）。
- 静态文件从 `website/` 目录直接读（html/css/js/资产）；页面是**纯静态 HTML**（每页一个 .html），
  交互用 vanilla JS。**不引入构建工具、不引入 CDN**。
- 动态端点（同一 server.py 里）：
  `POST /auth/login/` `POST /auth/register/` `POST /auth/logout/` `GET /me/` `POST /proxy/<path>/`
  `GET /download/list/`（扫 `website/media/downloads/`）。
- cookie 签名：HMAC-SHA256，密钥放 `website/.site_secret`（首次启动生成，0600，**不入库不上传**）。
  cookie 值 = `base64(json{access,refresh,exp})` + `.` + sig。httpOnly、SameSite=Lax。
- 对 phix 的所有请求**必须走信封**：把 `D:\moodsite\web\core\phix_e2e.py` **复制**为
  `website/core_e2e.py`（改 import 为本地），公钥固定文件放 `website/.phix_pubkey`。
- 端口：**8940**（`python server.py` 默认；`--port` 可改）。PHIX_SERVER 默认 `http://127.0.0.1:8931`，
  可用环境变量 `PHIX_SERVER` 覆盖。
- PHL 网页版代理只写 `website/app/` 与 `website/static/app/`；站点代理负责在 `/app/` 路由把
  `website/app/index.html` 原样返回（静态托管），**不要**在 server.py 里为 SPA 写业务逻辑。
