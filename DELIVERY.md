# 交付说明 · phix 官网 + 全端改版（2026-09-12）

> 本次交付：**phix 社团官网（含 PHL 网页版）** + 三端客户端的首启引导/头像改造 + 心履三端 v2 登录修复 + 可产出的安装包。
> 契约见 `CONTRACT.md`，设计见 `DESIGN.md`，构建矩阵见 `BUILD.md`。

---

## 1. 怎么跑起来

```powershell
# 1) phix 账号服务（8931）
cd D:\phix\server
.\.venv\Scripts\python.exe -X utf8 devtools\run_dev_server.py 8931     # 带放大限流；或 .\_lab\watchdog_8931.py 常驻自愈

# 2) 官网 + PHL 网页版（8940）
D:\phix\server\.venv\Scripts\python.exe -X utf8 D:\phix\website\server.py --port 8940
# 打开 http://127.0.0.1:8940/         （PHL 网页版在 /app/）

# 3) 自测
D:\phix\server\.venv\Scripts\python.exe -X utf8 D:\phix\website\smoke.py 8940        # 官网 26 项
D:\phix\server\.venv\Scripts\python.exe -X utf8 D:\phix\_lab\test_web_client.py 8940 # 官网↔客户端黄金链路 12 项
cd D:\phix\server; .\.venv\Scripts\python.exe -X utf8 devtools\run_all.py            # 服务端与两端客户端 710 项
```

## 2. 这一版做了什么

| 模块 | 内容 |
|---|---|
| **官网** | 11 个页面（首页/关于/心履/PHL/硬件占位/文档/支持/下载/登录/注册/个人中心）+ 产品下拉菜单 + 页脚；淡紫色板（`#9A2BE2` 系，按 logo 与首页照片实测校正）；hero 用你给的照片 |
| **PHL 网页版** | `/app/` 单页：日程（增删，只删自己建的）/ Edupage / ManageBac / 邮箱（凭据打码+眼睛）；数据全部经官网后端代理读写 phix |
| **会话** | 登录/注册后写 httpOnly 签名 cookie（`phix_access` 15 分钟 / `phix_refresh` 30 天）；过期自动续期一次；DEK **只放 cookie**，进程内零副本 |
| **个人中心** | 个人信息（用户名只读 + 头像上传）/ 心情记录 / 日程 / 凭据管理（`settings.accounts` 两层字典，按 section 分组，明文只在浏览器内存） |
| **三端客户端引导** | 首启先问「你有 phix 账号吗？」→ 登录（成功后拉一次同步）/ 注册 / **跳过** → 再进原有引导；已有会话（PLL/PHL 的 restore）直接跳过 |
| **头像** | 新增同步对象 `profile`（`display_name`/`avatar`/`updated_at`），三端可上传、各端可显示；心履网页端为圆形首字母占位（网页端拿不到 DEK） |
| **心履三端登录修复** | 原先发口令原文 → v2 账号永远登不进。新增**纯 Java SCrypt**（RFC 7914，零依赖）+ `PhixCrypto.authHashHex`，改为先取 keymaterial 再发 `auth_hash` |

## 3. 验证结果（全部实跑）

| 项 | 结果 |
|---|---|
| 服务端/两端客户端回归 `devtools/run_all.py` | **710 / 710**（13 个套件） |
| 官网冒烟 `smoke.py` | **26 / 26** |
| **官网↔客户端黄金链路** `_lab/test_web_client.py` | **12 / 12**（官网写→客户端看到、客户端加→官网读回、头像与凭据双向） |
| PHL `npm test` | **802 / 802**（含新引导/头像 13 项） |
| PLL 真实窗口 `_ui_test.py` | 退出码 0（phix 面板全绿，对象清单已含 profile/mood） |
| PLL 引导专项 `devtools/test_pll_onboard.py` | **32 / 32** |
| 心履 `manage.py test core` | **49** 全绿（1 跳过，与基线一致） |
| Java 代码审查（qwen3.8-max） | 16 文件：OK 12 / 风险 2 / 必崩 2 → **必崩 2 项已修** |
| 官网页面截图（headless Edge） | 11 页 + 下载页实测；首页/下载页人工复核通过 |

## 4. 这一波修掉的真 bug（都补了回归）

1. **官网信封 AAD 传了 None** → 官网写的密文客户端**永远解不开**（官网自环测试却全绿）。补齐 `phix/v1/object|user_id|name` 后黄金链路 12/12。
2. **用户 ID 未参与 AAD** → 同一对象在跨用户场景无法校验，同因修复。
3. **DEK 存网站进程内存** → 重启即丢、网站长期持钥；改为只存签名 cookie。
4. **静态文件整块读入内存** → 171MB 安装包 × 并发会吃爆内存；改为 64KB 流式发送。
5. **`HEAD` 返回 501** → 下载器/链接检查器拿不到头；补 `do_HEAD`。
6. **看门狗 env 缺 TEMP** → SQLite 大事务报 `unable to open database file`（900KB 对象必挂，小请求全正常）；改为继承完整环境再覆盖限流。
7. **看门狗竞态双拉起** → 两个实例同时监听，请求随机落；改为起前精确清旧 + 抑制重复启动。
8. **Android `InputStream.readAllBytes()`**（API 33+）→ 低版本选头像必崩；改分块读。
9. **Android 登录失败后按钮永久禁用** → 用户无法重试；改回启用。
10. **PLL/PHL 默认对象数从 6→8** → `test_pll_session` 断言同步更新。

## 5. 构建产物

下载页只列**存在**的文件；缺失的灰显「构建中」，**不伪造**。
交付副本（只增不覆盖，放在新建子目录里）：`C:\Users\Administrator\Desktop\clients\phix-20260912\`

| 产物 | 状态 | 说明 |
|---|---|---|
| `xinlv-windows-setup.exe` | ✅ 74.3 MB | jpackage `--type exe`（WiX），2026-09-12 17:17 |
| `xinlv-windows.zip` | ✅ 72.7 MB | 同一份的 portable app-image 打包 |
| `xinlv-android.apk` | ✅ 5.3 MB | `gradlew assembleRelease` **签名** APK，17:15 |
| `xinlv-macos.dmg` | ✅ 114.5 MB | 在 Mac 构建机（`192.168.5.13`）用 JavaFX + jpackage 出 DMG，17:23；未签名（首次打开需右键→打开） |
| `phl-windows-setup.exe` | ✅ 163.8 MB | electron-builder NSIS |
| `phllite-windows-setup.msi` | ✅ 52.2 MB | **2026-09-21 重出**：PyInstaller 出 app exe，再用 **WiX 3.14**（`installer\PingheLauncherLite.wxs`）编译成 MSI；按用户安装（无需管理员）。**只出安装版，不再提供便携版 exe** |
| `phl-macos.dmg` | ✅ 191.9 MB | 在 Mac 上**免 sudo 装 Node v20.19.0**（`~/opt/node`）后 electron-builder 打包；未签名 |
| `phllite-macos.dmg` | ✅ 36.3 MB | **2026-09-21 重出**：在 Mac 构建机（`192.168.5.13`）用 `scripts\macos_build.py` 同步源码后 PyInstaller 打包；本次为 Intel（x86_64），Apple Silicon 走系统转译；未签名 |

### 5.1 在 Mac 构建机上新增了什么（如实登记）

| 内容 | 位置 | 说明 |
|---|---|---|
| Node v20.19.0 | `~/opt/node`（用户级，未 sudo） | electron 主进程要求 node≥22.12，构建时有 EBADENGINE 警告但产物正常；需要时可用更高版本替换 |
| Python 用户级包 | `~/.local/lib/python3.12/site-packages` | `pyinstaller`、`pywebview`、`pyobjc-framework-WebKit` 及 PLL 运行时依赖（requests / beautifulsoup4 / edupage-api / keyring / python-docx / pystray / Pillow / PyYAML / openai / anthropic） |
| 源码镜像 | `~/Documents/PLL`、`~/Documents/PH-Launcher` | 仅源码（排除了 `.git`、`node_modules`、测试环境、用户数据）；`~/Documents/XinLv` 是原有的心履目录，未动 |

> 上游 npm/GitHub 被墙：electron 二进制通过 `ELECTRON_MIRROR` / `ELECTRON_BUILDER_BINARIES_MIRROR` 指向 npmmirror 拿到。

> **重要副作用**：本轮构建**真实编译**了改动过的 Java 代码 ——
> `D:\moodsite\windows` 的 `mvn -q clean package` 与 `D:\moodsite\android` 的 `gradlew assembleRelease` 都 **exit 0**，
> 等于把代码审查（无 JDK 时做不了编译验证）那一步补齐了；`ScryptUtil`/`PhixCrypto`/`ApiClient`/引导页/头像改动均通过编译。

## 6. 端口与 Cloudflare（单端口架构）

**一个域名 = 一个源站端口**，所以官网与 API **共用同一个端口**（本机与 `.41` 都是 **8940**）：

```
浏览器 / 客户端 ──► http(s)://<域名或 192.168.5.41>:8940
                        │
        ┌───────────────┴────────────────┐
        │ /api/v1/*  与  /healthz        │ → 字节透传给 phix 服务（127.0.0.1:8931）
        │ 其它一切（含 /proxy/、/app/、下载）│ → 官网自身（静态页 + 后端代理）
        └────────────────────────────────┘
```

- **透传不解析**：`/api/*` 的请求体连密文信封一起原样转发、响应原样回；这一层看不到任何明文，
  也不碰 DEK/令牌语义（E2E 加密对它透明）。
- 于是桌面客户端（PHL / PLL / 心履）与网页端**可以填同一个地址**：
  局域网填 `http://192.168.5.41:8940`，接入 Cloudflare 后填 `https://<你的域名>`。
- Cloudflare 侧只需把域名指到 **8940** 这一个源站端口（Tunnel 映射 `http://localhost:8940` 即可）。
- 收紧建议（可选）：接入域名后把 phix 的 `8931` 收回只听 `127.0.0.1`
  （`python -X utf8 server\deploy\deploy.py --bind-host 127.0.0.1`），
  这样对外只剩 8940 一个口，API 只能经官网这一层进入。

---

## 7. 已知取舍与遗留

1. **PHL 网页版不做**：AI、本地文件同步、设置页（按你的要求）。
2. **官网改密码**：网页端拿不到 DEK，页面提示「请到客户端修改」，不伪造功能。
3. **mac/Android 未构建**：只做了代码级审查（报告 `_lab/review/REVIEW-MAC-ANDROID.md`），本机无对应工具链。
4. **ScryptUtil 无编译验证**：报告里给了用 Python `hashlib.scrypt` 生成对拍向量的命令，等有 JDK 的机器跑一次即可闭环。
5. **Range 请求不支持**（`Accept-Ranges: none`）：大文件不能断点续传；需要时再补。
6. **心履新注册走 v1**（无 scrypt 的旧路径无法一次到位），登录已支持 v2。
7. 三个业务仓库**零提交**，改动都在工作区。

---

## 8. 用户已确认的决定（2026-09-12，勿擅自更改）

| 事项 | 用户的决定 | 说明 |
|---|---|---|
| 页脚「联系我们」的公开程度 | **不精简** | 保留邮箱（norine.liu@icloud.com / huaziqian40@gmail.com）、电话（18901712280 / 13675821816）、微信（Norine2010 / int_32_2147483647）、B站。站点经 Cloudflare 公网可见，这是用户明确同意公开的 |
| 先前的 18 个"临时口令"迁移账号 | **不清理** | 它们会被同名账号的「原密码特判」接管：老用户用原密码登录 → 官网/心履比对旧哈希通过 → phix 发现同名账号已存在 → 走「已被注册→不覆盖→重试校验」分支。不影响正常使用，故保留 |
| 心履生产机 `.35` 的部署 | **尚未批准** | 心履侧「登录时自动迁移」代码已就绪并通过 22/22 测试，但**未部署**；部署前需先在心履库执行 `manage.py migrate`（0014 补 `phix_user_id` / `show_migration_notice` 两列） |
