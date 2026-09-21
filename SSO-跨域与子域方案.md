# 跨域免密登录：一次性码 + 子域（xinlv.phix.ing）方案

> 需求原文：「登录**心履**（xin-lv.com）、**Pinghe Launcher 网页端**（phix.ing/app/）、
> **phix 官网**（phix.ing）中的任何一个，其它两个都自动登录，不需要手动再登一次」，
> 并且「登录态用 cookie 存在本地」。
>
> 本文写清：**已经做好的**（一次性码，本文档 §2）、**只给补丁与步骤、没有真去动的**
> （子域方案，§4 —— DNS 在 Cloudflare，用户未授权改动，所以只给方案），
> 以及 cookie 的"本地保存"到底是什么意思（§3，含过期与退出清理）。

---

## 1. 现状事实（先讲清楚为什么有一半是"不用做"）

| 事实 | 说明 |
|---|---|
| 官网与 PHL 网页端**同域** | 都在 `phix.ing`，同一台机（192.168.5.41）、同一个 `phix-site.service`、同一个 `website/` 目录 |
| 所以**同域 cookie 天然共享** | `/app/` 与官网共用 `phix_access` / `phix_refresh`：官网登录后直接开 `/app/` **无需再登**（已在 §2.4 实测） |
| 心履在**另一个注册域** | `xin-lv.com`，跑在 192.168.5.35，Django + waitress，Cloudflare Tunnel 出网 |
| 浏览器的硬约束 | 注册域之间**不能**共享 cookie。跨域搬登录态只有两条路：**一次性码**（已做）或**挪到子域**（只给方案） |

---

## 2. 已实现方案：一次性 SSO 码（跨域，当前生产可用）

### 2.1 接口

**phix API（`API/sso.py`，源码 `D:\phix\server\api\sso.py`，前缀 `/api/v1`）**

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/v1/auth/sso/code` | **Bearer 会话** 或 **`X-Phix-Service-Key`** | 换一枚一次性码。返回 `{ok, code, expires_in:120, audience, single_use:true, issued_via}` |
| POST | `/api/v1/auth/sso/redeem` | **`X-Phix-Service-Key`**（默认必须）+ `code` | 用码换令牌。body `{code, site, device}`；响应与 `/auth/login` **完全同构**（access/refresh/key_wrap/kdf_salt/key_check/…），另加 `sso:{audience,issued_via,single_use,minted_ago}` |

**官网后端（`D:\phix\website\server.py`）**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/sso/code/` | 用 cookie 会话换码（转发 `auth/sso/code`，带 Bearer） |
| POST | `/auth/sso/redeem/` | 码 → 本站会话（写 `phix_access`/`phix_refresh`/`phix_hint`，JSON 返回） |
| GET | `/auth/sso/enter/?code=&next=` | **浏览器入口**：兑换成功 → 302 到 `next`（带 `?sso=phix`） |
| GET | `/sso/to-xinlv/?next=&soft=1` | **浏览器入口**：已登录 → 签码并 302 到心履兑换页；`soft=1` 时未登录就直接打开心履 |
| POST | `/auth/unlock/` | 免密会话没有 DEK → 输一次口令把 DEK 补进 cookie（§2.3） |

**心履（`D:\moodsite\web\core\phix_sso.py`）**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/phix/sso/?sso=<码>&next=` | 官网/网页端跳过来 → 调 phix 兑换 → 落到**本地账号** → `django.contrib.auth.login()` |
| GET | `/phix/sso/start/?next=/account/` | 心履已登录 → 让 phix 签码 → 302 到官网 `/auth/sso/enter/` |

### 2.2 安全设计（逐条对应一个攻击面）

| 性质 | 做法 |
|---|---|
| 码不含凭据 | 256 位随机串（`secrets.token_urlsafe(32)`）；服务端**只存 SHA-256 摘要**；码里没有用户名、令牌、DEK |
| 单次有效 | 兑换在锁内 `pop`（"用后即焚"，不是打标记） |
| 120 秒过期 | `PHIX_SSO_TTL`；过期条目在下次操作时顺手 gc；过期与"不存在"回**同一句话**（不给"这个码曾经是真的"的 oracle） |
| 绑来源站点 | 码写死 `audience`（`phix-site` / `xinlv`），兑换 `site` 必须一致；**站点不符不消耗码**（否则拿别人码发一次错请求就能烧掉它） |
| 光有码换不走 | 兑换默认要求 `X-Phix-Service-Key`（`PHIX_SSO_REQUIRE_SERVICE_KEY=1`）——两个合法兑换方本来就都有它 |
| 绑 IP 前缀（可选） | `PHIX_SSO_IP_BIND = off(默认) / prefix / exact`。**默认关**是有意的：签发方看到的是**用户浏览器** IP，兑换方看到的是**另一台服务器**（心履 .35），绑死会挡掉正常流程。无论开关，签发 IP 前缀都记进条目供审计 |
| 限流 | 签发：每 IP / 每用户 `PHIX_SSO_MINT_LIMIT`（默认 30/小时）+ 每人同时待兑换上限 10；兑换：每 IP `PHIX_SSO_REDEEM_LIMIT`（60/小时）；**失败**另有 `PHIX_SSO_FAIL_LIMIT`（20 次/15 分钟）窗口，超了直接 429 |
| 不落日志 | 日志只写摘要前 8 位（`sid=ab12cd34`），码全文绝不写日志（`api/tests_sso.py::test_code_not_in_logs` 断言 + `_lab/sso/*` 的 grep 证据） |
| 兼容 E2E | 兑换走**同一条 `_issue_session()` 令牌发放路径**（没有第二套令牌代码），且经应用层信封传输；**码里从不携带 DEK**，服务端本来也没有 DEK |

### 2.3 一个必须说清楚的取舍：免密登录来的官网会话**没有 DEK**

- 官网正常登录时，DEK 是用**口令**解开的（`KEK ← scrypt(口令)`），解出来放进签名 cookie 的 `d` 字段，
  所以 `/me/` 能拿头像、`/app/` 能读日程/课表、`/account/` 能看凭据。
- 一次性码**刻意不携带任何凭据**（含 DEK），心履那边也**从不持有** phix 的 DEK
  （心履的设计原则是"第一台服务器拿到的越少越好"）。因此从心履免密过来的官网会话是
  **身份会话**：能登录、能看个人中心、能改密码之外的常规操作，但**读不了端到端加密的对象**
  （头像/心情/日程/四平台凭据在服务端仍是密文）。
- 为此提供 `POST /auth/unlock/`：输**一次**口令 → 本地解 `key_wrap` → 用 `key_check` 做持有性证明
  → 把 DEK 写回签名 cookie。**进程里不留副本**，与正常登录同一套机制；`/me/` 增加
  `dek_unlocked` 字段供页面判断要不要提示解锁。

> 没有做的替代方案（诚实记录）：把 DEK 长期存进官网服务端的"密钥保险箱"，这样免密会话也能直接读数据。
> **没做**，因为它会让网站服务器长期持钥，直接推翻「DEK 只在签名 cookie 里、进程零副本」这条设计。
> 需要"免密且能读数据"时，请改用 §4 的子域方案（同一个 `phix_access` cookie 直接带 DEK 过去）。

### 2.4 实测（原始输出见交付报告）

- 官网登录 → `/app/` 直接 200（同域，无需再登）；
- 官网 → `/sso/to-xinlv/` → 心履 302 + **心履自己的 `sessionid`**（Set-Cookie 原文已贴）；
- 心履 → `/phix/sso/start/` → 官网 `/auth/sso/enter/` → **官网 `phix_access`/`phix_refresh`/`phix_hint`** 落地；
- 码第二次用失败、伪造码失败、缺服务密钥失败、站点不符失败且不消耗码、连续失败触发 429、码不落日志。

---

## 3. 「登录态用 cookie 存在本地」是什么意思

| 站点 | cookie | 属性 | 里面是什么 | 过期 | 退出时怎么清 |
|---|---|---|---|---|---|
| phix 官网 / `/app/` | `phix_access` | `HttpOnly; SameSite=Lax; Path=/` | `base64(json{a:access, r:refresh, e:exp, d:DEK, u:用户名})` + **HMAC-SHA256 签名**（密钥 `website/.site_secret`，本机生成 0600） | 15 分钟看一次（每次 `/me/` 或 `/proxy/` 续期就回写） | `POST /auth/logout/` → 调 phix 注销会话 + 把两个 cookie `Max-Age=0` |
| 同上 | `phix_refresh` | 同上 | 同上（寿命更长的那份） | 30 天 | 同上 |
| 同上 | `phix_hint` / `phix_hint_user` | **非 HttpOnly**、`SameSite=Lax` | 只有 `1` 和用户名，**绝不放令牌/DEK** | 30 天 | 同上 |
| 心履 | `sessionid` | `HttpOnly; SameSite=Lax` | Django 会话键（会话内容存**心履自己的库** `django_session` 表） | 默认 2 周 | `/logout/` |

**"本地保存"的确切含义**：

- 官网这边**服务端不存任何会话状态**——登录只是把 phix 发的令牌 + 本机解出的 DEK 塞进**签名 cookie**，
  下次请求由浏览器带回、服务端验签即用。所以"登录态在浏览器本地"，重启网站进程不掉线，
  换一台浏览器就要重新登。服务端只保留了**令牌吊销表**（`TokenSession`，用于注销/撤销），
  但它不是会话状态的载体：cookie 没了就是没登录。
- 心履这边是 Django 标准会话（cookie 里只有会话键，内容是服务端表里的一行）——
  这是心履原有机制，**没有改动**；跨域免密登录落地后种的就是这个 cookie。
- **安全代价（如实说明）**：官网的签名 cookie 里装着 access/refresh **和 DEK**（DEK 只以明文形式
  存在于浏览器 cookie 与请求内存中）。`HttpOnly` 让 JS 读不到，签名让它不可伪造；
  但**能读该浏览器磁盘的人可以拿到 DEK**。这是"网站作为客户端代理"这一既有取舍的延续，
  不是本次引入的（见 `website/server.py` 顶部与 README）。
- 生产环境务必用 HTTPS（现在是 Cloudflare Tunnel）：否则 cookie 与 DEK 在内网明文过线。
  `SECURE_COOKIES=True` / `SESSION_COOKIE_SECURE` 在 .35 的 `.env` 里已按 HTTPS 打开。

---

## 4. 备选方案：把心履 web 挪到 `xinlv.phix.ing`（**只给补丁与步骤，未执行**）

### 4.1 为什么需要它

同父域（`.phix.ing`）的 cookie 可以跨子域共享，于是**不需要任何一次性码**：
在官网登录后，浏览器访问 `xinlv.phix.ing` 也会带上 `phix_access`，
心履后端只要会验这个 cookie 就能"零点击自动登录"。

### 4.2 目标拓扑

```text
                    Cloudflare（DNS + Tunnel，公网 HTTPS）
                      │
      ┌───────────────┴───────────────────────────┐
      │ phix.ing / www.phix.ing                   │ xinlv.phix.ing
      │ → Tunnel → 192.168.5.41:8940（官网+API）   │ → Tunnel → 192.168.5.35:8000（心履）
      └───────────────────────────────────────────┘
   两边 cookie 作用域 = .phix.ing（官网签发的 phix_access 对两个子域都可见）
```

### 4.3 需要的改动（**一个都没做**，下面是精确清单）

**① Cloudflare（DNS + Tunnel）— 需要用户授权**

1. DNS：新增 `xinlv` 记录，类型 `CNAME`，目标为 Tunnel 的 `<UUID>.cfargotunnel.com`，代理状态**已代理**（橙色云）。
   （若 .35 上是本机 cloudflared，则在**该机**的 tunnel 配置里加一条 ingress）
2. .35 的 cloudflared ingress（`~/.cloudflared/config.yml`）：
   ```yaml
   ingress:
     - hostname: xinlv.phix.ing
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```
   改完 `sudo systemctl restart cloudflared`。
3. 想两边都能从 `xinlv.phix.ing` 打开，也可以把 `phix.ing` 的规则一并保留（现状不动）。

**② 心履 Django 配置（.35 的 `/home/hzq/xinlv-web/.env`）**

```ini
DJANGO_ALLOWED_HOSTS=xin-lv.com,www.xin-lv.com,xinlv.phix.ing,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://xin-lv.com,https://www.xin-lv.com,https://xinlv.phix.ing,https://phix.ing
SESSION_COOKIE_DOMAIN=.phix.ing
CSRF_COOKIE_DOMAIN=.phix.ing
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
PHIX_SITE_URL=https://phix.ing
```
> 代码侧**已经留好开关**（本次改动，默认空 = 现状不变）：
> `moodsite/settings.py` 里
> `SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN","").strip() or None`，
> `CSRF_COOKIE_DOMAIN` 同理。所以上面这段**只改 .env，不用改代码**。
> 改完：`echo 000000 | sudo -S systemctl restart xinlv.service`。

**③ phix 官网 cookie 的 Domain（.41 的 `phix-site.service`）**

```ini
# /etc/systemd/system/phix-site.service 的 [Service] 段加一行：
Environment=PHIX_COOKIE_DOMAIN=.phix.ing
```
> 代码侧同样已留好开关：`website/server.py` 的 `COOKIE_DOMAIN = os.environ.get("PHIX_COOKIE_DOMAIN","").strip()`，
> 目前只作用于**非 httpOnly 的提示 cookie**（`phix_hint`）。
> 若要让 `phix_access` 也跨子域共享，需要把 `_make_cookie_header` 的 domain 也传进去
> —— **这一步没有做**，因为它把含 DEK 的会话 cookie 暴露给整个 `*.phix.ing` 的所有子域
> （含任何将来加的子域），信任边界会被显著放大。要上就必须先评估这一点。
> 改完：`sudo systemctl daemon-reload && sudo systemctl restart phix-site.service`。

**④ 验证清单（做完上面才跑）**

```bash
# 1) 两个域名都通
curl -s -o /dev/null -w '%{http_code}\n' https://xinlv.phix.ing/login/
curl -s -o /dev/null -w '%{http_code}\n' https://phix.ing/
# 2) 官网登录后，cookie 的 Domain 是 .phix.ing
curl -s -D - -o /dev/null -X POST https://phix.ing/auth/login/ \
  -H 'Content-Type: application/json' -d '{"username":"...","password":"..."}' | grep -i set-cookie
# 3) 带着这个 cookie 打心履，应当直接是登录态（心履侧需要接一个"验 phix cookie"的后端，
#    见 §4.5）
```

### 4.4 回滚

| 改动 | 回滚 |
|---|---|
| Cloudflare DNS/Tunnel | 删掉 `xinlv` 记录 / ingress 条目，重启 cloudflared |
| 心履 .env | 删掉 `SESSION_COOKIE_DOMAIN`/`CSRF_COOKIE_DOMAIN` 两行（或整段回退备份文件），重启服务 |
| phix-site 单元 | 删掉 `Environment=PHIX_COOKIE_DOMAIN` 行，`daemon-reload` + 重启 |

**回滚要点**：`SESSION_COOKIE_DOMAIN` 一改，**所有已登录用户的老 cookie 立即失效**（域不匹配），
需要重新登录一次。所以这一步应当挑低峰做，并先 `cp .env .env.bak-<日期>`。

### 4.5 子域方案还差什么代码（**没做**）

同父域 cookie 只解决"登录态可见"，心履仍需要一个后端把 `phix_access` 认下来：

1. 读 `phix_access` → 本地验 HMAC 签名**做不到**（签名密钥 `.site_secret` 在 .41，不能给 .35）；
   可行做法是拿 cookie 里的 access 令牌去 phix 调 `GET /api/v1/auth/me`（或 `/auth/introspect`，
   需服务密钥）确认真伪 → 再落到本地账号（复用本次新增的 `core/phix_sso.local_user_for()`）。
2. 心履的 `SESSION_COOKIE_DOMAIN=.phix.ing` 会让心履自己的 `sessionid` 也跨子域——
   这是有意的（`phix.ing` 下的页面也能共享心履登录态），但要接受"任一子域被攻破即全会话暴露"。

**为什么本轮没做**：它需要改 DNS/反代（用户未授权），而且把含 DEK 的 cookie 作用域放大到父域
是**安全上的实质退让**；一次性码已经能让"任一端登录 → 其它端自动登录"这条需求成立，
所以先交付码方案，子域作为可选升级。

---

## 5. 配置项速查

| 位置 | 变量 | 默认 | 说明 |
|---|---|---|---|
| phix 服务端 | `PHIX_SSO_TTL` | 120 | 码有效期（秒） |
| phix 服务端 | `PHIX_SSO_AUDIENCES` | `phix-site,xinlv` | 允许的站点标识 |
| phix 服务端 | `PHIX_SSO_REQUIRE_SERVICE_KEY` | 1 | 兑换是否必须带服务密钥 |
| phix 服务端 | `PHIX_SSO_IP_BIND` | off | `off`/`prefix`/`exact` |
| phix 服务端 | `PHIX_SSO_MINT_LIMIT` / `_WINDOW` | 30 / 3600 | 签发限流 |
| phix 服务端 | `PHIX_SSO_REDEEM_LIMIT` / `_WINDOW` | 60 / 3600 | 兑换限流 |
| phix 服务端 | `PHIX_SSO_FAIL_LIMIT` / `_WINDOW` | 20 / 900 | 失败限流 |
| phix 服务端 | `PHIX_SSO_LIVE_PER_USER` | 10 | 每人同时待兑换码上限 |
| 官网 | `XINLV_SITE_URL` | `https://xin-lv.com` | 跳心履用 |
| 官网 | `PHIX_COOKIE_DOMAIN` | 空 | 提示 cookie 的 Domain（子域方案用） |
| 心履 | `PHIX_SSO_ENABLED` | 1 | 免密登录开关 |
| 心履 | `PHIX_SITE_URL` | `https://phix.ing` | 跳官网用 |
| 心履 | `RL_SSO_N` | 20 | 免密兑换限流（5 分钟窗口） |
| 心履 | `SESSION_COOKIE_DOMAIN` / `CSRF_COOKIE_DOMAIN` | 空 | 子域方案用 |

> `PHIX_SERVER` / `PHIX_SERVICE_KEY` 三处必须一致：phix 服务端 `~/.config/phix/env`、
> 官网 `phix-website/.service_key`、心履 `.env`。生产实测三者 SHA-256 指纹一致
> （见交付报告，只贴指纹不贴密钥）。
