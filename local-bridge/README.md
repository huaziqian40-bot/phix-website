# phix 本机直连服务（phix-local-bridge）

网页端 <https://phix.ing/app/> 默认让**服务器**代抓学校平台（EduPage / ManageBac / 邮箱）。
如果服务器所在网络连不上学校平台，网页端就抓不到数据。

`local-bridge` 是一个**可选的**本机小服务：跑在你自己的电脑上，用**你自己的网络和 IP**
去抓学校平台，网页端探测到它就把抓取交给它；没启动、探测不到、或者请求失败时，
网页端**自动回到服务器抓取**，你什么都不用改。

> 这是**可选加速 / 可用性增强**，不是必须装的东西。不装 = 和现在完全一样。

---

## 1. 安装依赖（只做一次）

需要 Python 3.10+。

```bat
python -m pip install requests beautifulsoup4 edupage-api
```

或者用随附的清单文件：

```bat
python -m pip install -r local-bridge\requirements-webapp.txt
```

三个包都是**在函数内部延迟 import**：装不上时对应平台只是抓不到（网页端会显示原因），
服务本身照样能起来。

## 2. 启动

**Windows 一键启动**：双击 `local-bridge\start-bridge.cmd`
（它会自动找 Python、缺依赖时自动装、然后起服务）。

**命令行**：

```bat
python local-bridge\bridge.py              :: 默认 127.0.0.1:38123
python local-bridge\bridge.py --port 40000 :: 换端口
set PHIX_BRIDGE_PORT=40000 && python local-bridge\bridge.py   :: 或用环境变量
```

启动成功会打印：

```
phix-local-bridge v1 已启动：http://127.0.0.1:38123
  仅监听本机回环（127.0.0.1），不对外网开放；凭据只在内存里用，不落盘、不写日志。
```

**只监听 `127.0.0.1`，绝不监听 `0.0.0.0`** —— 同局域网的其他设备访问不到它。
关掉窗口即停止服务。

## 3. 验证

```bat
curl http://127.0.0.1:38123/ping
```

期望：

```json
{"ok": true, "service": "phix-local-bridge", "version": 1}
```

然后打开 <https://phix.ing/app/>（刷新一次），进「⚙️ 设置 → 数据来源」，
应当显示 **「本机直连」**；把服务窗口关掉再刷新，应当回到 **「服务器抓取」**。

浏览器允许 HTTPS 页面访问 `http://127.0.0.1`（回环地址属"可信来源"，
不受混合内容拦截），所以 https 站点可以直接调它。

## 4. 接口（冻结）

| 方法 | 路径 | 请求体 | 响应 |
| --- | --- | --- | --- |
| GET | `/ping` | — | `{"ok":true,"service":"phix-local-bridge","version":1}` |
| POST | `/data`（可加 `?force=1` 绕过 5 分钟缓存） | `{"accounts":{…}}` | 与服务器 `GET /app/data/` **完全相同**的结构（`edupage`/`managebac`/`mail`/`meta`） |
| POST | `/mail/<uid>` | `{"accounts":{…}}` | 与服务器 `GET /app/mail/<uid>/` 相同的结构 |

`accounts` 就是同步对象 `settings.accounts` 里解出来的那份映射，由**网页端在请求体里传过来**：

```json
{"accounts": {"edupage": {"username": "…", "password": "…"},
              "managebac": {"username": "…", "password": "…"},
              "mail": {"username": "…", "password": "…"}}}
```

错误响应与服务器同款：`401/409/404/502` + `{"ok":false,"error":{"code","message"}}`。

CORS 白名单：`https://phix.ing`、`https://www.phix.ing`，以及 `http://127.0.0.1:*` /
`http://localhost:*`（本地调试）。`OPTIONS` 预检返回 `204` 并回显 `Origin`。

## 5. 隐私与安全

* 平台账号口令**只在内存里用一次**，请求处理完即丢；本进程**不落盘、不写日志文件**，
  标准输出也只有启动/停止那两行中文提示（HTTP 请求日志被显式关闭）。
* 缓存（5 分钟）键是「平台账号名」，**不含口令**，与服务端 `webapp_data` 一致。
* 只绑回环地址；CORS 只放行 phix 官网来源，其它网页（含恶意页面）拿不到响应。
* 抓取逻辑直接复用服务端同一套 `webapp_data.py` / `webapp_managebac.py` /
  `webapp_mb_parse.py`（本目录是它们的副本，改动请改服务器那份再同步过来），
  本目录**没有**任何平台协议实现。

## 6. 文件

| 文件 | 作用 |
| --- | --- |
| `bridge.py` | 本机服务本体（标准库 `http.server` + `ThreadingHTTPServer`） |
| `webapp_data.py` | 抓取层（服务器同款副本，函数签名不变） |
| `webapp_managebac.py` | ManageBac 客户端（requests + bs4） |
| `webapp_mb_parse.py` | ManageBac HTML 解析 |
| `start-bridge.cmd` | Windows 一键启动 |
| `requirements-webapp.txt` | 三个第三方依赖的清单 |

`local-bridge/` **不部署到服务器**（deploy.py 的排除名单里有它）——它属于用户本机。
