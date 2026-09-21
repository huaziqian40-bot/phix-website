"""把 phix 官网幂等部署到社团 Linux 服务器（192.168.5.41:8940）。

用法（任意目录都能跑，脚本自己定位本地 website/）：

    python -X utf8 D:\\phix\\website\\deploy.py                  # 上传 + 装单元 + 自检
    python -X utf8 D:\\phix\\website\\deploy.py --skip-upload     # 只装单元 + 自检
    python -X utf8 D:\\phix\\website\\deploy.py --verify-only     # 只看现状（不改任何东西）

设计要点（都是踩过的坑）：

1. **上传幂等**：SFTP 逐个文件传，远端大小一致就跳过；小于 4MiB 的再比 sha256
   （两端都算，避免"大小一样、内容不同"漏更新）。大文件只看大小 —— 那 8 个安装包
   共 ~760MB，每次重跑都下载回来比对是浪费。断了直接重跑就是断点续传。
2. **`.site_secret` / `.phix_pubkey` 永不上传、永不覆盖**：它们是**每台机器自己生成**的。
   `.site_secret` 由 server.py 首次启动 `_load_or_create_secret()` 生成（0600）；
   `.phix_pubkey` 由 `_get_server_pk()` 从**本机** 8931 的 `/api/v1/ping` 取公钥后固定
   —— 从别的机器拷过来必然对不上（core_e2e.pin_key 会直接抛 ServerKeyError）。
   本地这份（0 字节）也不传，免得把远端的有效密钥带坏。
3. **systemd 用 sudo -S 脚本模式**：密码走 stdin 第一行，脚本体走同一条管道，
   **不把密码拼进 heredoc**。老写法 `sudo -S bash -c "...<<EOF..."` 的 heredoc
   会被吃掉，表现是"什么都没发生"（见 _recon/remote_test_limits.py 顶部的记录）。
4. **只碰 phix-site.service**：phix.service 不 restart / 不 stop，不写 drop-in；
   `~/.config/phix/env`、`db.sqlite3` 一个字节不动；不装任何 Python 包
   （venv 里已有 cryptography 50.0.1，server.py + core_e2e.py 只用它 + 标准库）。
5. **绑定地址**：server.py 默认只绑 127.0.0.1（安全默认），本机要局域网直连 +
   Cloudflare 回源，故 ExecStart 显式传 `--host 0.0.0.0`。若上传的 server.py 不支持
   `--host`（老版本），脚本会退化成只传 `--port` 并**明确警告**，绝不静默。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import posixpath
import sys
import time
import urllib.request
from pathlib import Path

import paramiko

# ---- 目标环境（用户给定）----
HOST, USER, PWD = "192.168.5.41", "phix", "000000"
REMOTE_ROOT = "/home/phix/phix-website"
VENV_PY = "/home/phix/phix-server/.venv/bin/python"
PHIX_SERVER = "http://127.0.0.1:8931"
SITE_PORT = 8940

UNIT_NAME = "phix-site.service"
UNIT_PATH = f"/etc/systemd/system/{UNIT_NAME}"

# ---- 本地源与排除规则 ----
LOCAL_DIR = Path(__file__).resolve().parent
EXCLUDE_DIRS = {"__pycache__", ".git"}
#: local-bridge 是**用户自己电脑上**的可选本机抓取服务（只绑 127.0.0.1），
#: 部署到服务器没有意义（服务器上既没有用户的口令、也没有用户的网络）。
#: 随附的 webapp_*.py 副本与服务器那份内容相同，不传也不会影响站点。
EXCLUDE_REL_DIRS = {"media/logs", "local-bridge"}
EXCLUDE_FILES = {".site_secret", ".phix_pubkey", ".service_key", "content.json",
                 "admin_audit.log", ".legacy_users.json", "logs.json", "logs.json.tmp",
                 Path(__file__).name}
EXCLUDE_EXTS = {".pyc"}
HASH_LIMIT = 4 * 1024 * 1024      # 小于这个大小才做内容比对

MB = 1024.0 * 1024.0


def human(n: int) -> str:
    return f"{n / MB:.1f} MB"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files() -> tuple[list[tuple[str, Path, int]], list[str]]:
    """返回 (要传的文件, 被排除的条目说明)。"""
    out: list[tuple[str, Path, int]] = []
    excluded: list[str] = []
    for root, dirs, files in os.walk(LOCAL_DIR):
        kept = []
        for d in sorted(dirs):
            rel_d = Path(root, d).relative_to(LOCAL_DIR).as_posix()
            if d in EXCLUDE_DIRS or rel_d in EXCLUDE_REL_DIRS:
                excluded.append(f"{rel_d}/ （目录）")
            else:
                kept.append(d)
        dirs[:] = kept
        for name in sorted(files):
            rel = (Path(root) / name).relative_to(LOCAL_DIR).as_posix()
            if name in EXCLUDE_FILES or Path(name).suffix in EXCLUDE_EXTS:
                excluded.append(rel)
                continue
            p = Path(root) / name
            out.append((rel, p, p.stat().st_size))
    return out, excluded


class Remote:
    """一个 SSH 连接 + 一个 SFTP 通道；所有远端动作都从这里走。"""

    def __init__(self) -> None:
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(HOST, username=USER, password=PWD, timeout=20)
        self.sftp = self.client.open_sftp()
        # 默认 32KiB 的请求太小，单连接带宽上不去；OpenSSH 支持到 256KiB。
        try:
            paramiko.SFTPFile.MAX_REQUEST_SIZE = 1 << 17
        except Exception:  # noqa: BLE001  不同版本属性名可能变，无所谓
            pass

    def close(self) -> None:
        try:
            self.sftp.close()
        finally:
            self.client.close()

    # ---- 普通命令（phix 用户身份）----
    def sh(self, script: str, timeout: int = 120) -> str:
        i, o, e = self.client.exec_command("bash -s", timeout=timeout)
        i.write(script)
        i.channel.shutdown_write()
        return (o.read().decode("utf-8", "replace")
                + e.read().decode("utf-8", "replace"))

    # ---- 需要 root 的命令：密码走 stdin 第一行，脚本体走同一条管道 ----
    def sudo(self, script: str, timeout: int = 180) -> str:
        i, o, e = self.client.exec_command("sudo -S -p '' bash -s", timeout=timeout)
        i.write(PWD + "\n" + script)
        i.channel.shutdown_write()
        return (o.read().decode("utf-8", "replace")
                + e.read().decode("utf-8", "replace"))

    def size(self, path: str) -> int | None:
        try:
            return self.sftp.stat(path).st_size
        except IOError:
            return None

    def sha256(self, path: str, length: int) -> str:
        h = hashlib.sha256()
        with self.sftp.open(path, "rb") as f:
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
        return h.hexdigest()

    def _mkdir(self, path: str) -> None:
        try:
            self.sftp.stat(path)
        except IOError:
            self.sftp.mkdir(path)

    def mkdirs(self, rel_dir: str = "") -> None:
        """确保 REMOTE_ROOT（以及 rel_dir 这层子目录）存在。

        坑：rel_dir 传 "" 时旧写法什么都不做，于是第一个文件就 ENOENT —— 根目录
        本身也得建。
        """
        cur = REMOTE_ROOT
        self._mkdir(cur)
        for part in (p for p in rel_dir.split("/") if p):
            cur = posixpath.join(cur, part)
            self._mkdir(cur)

    def put(self, local: Path, remote_path: str, total: int, label: str) -> None:
        start = time.time()
        state = {"next": 0}

        def cb(sent: int, tot: int) -> None:
            pct = int(sent * 100 / tot) if tot else 100
            if pct >= state["next"] or sent >= tot:
                state["next"] = pct + 5
                el = max(time.time() - start, 1e-6)
                print(f"        {pct:3d}%  {sent / MB:7.1f}/{tot / MB:.1f} MB"
                      f"  {sent / el / MB:6.1f} MB/s", flush=True)

        self.sftp.put(str(local), remote_path, callback=cb, confirm=True)


def build_unit(host_supported: bool) -> str:
    # 必须显式带 --host 0.0.0.0：server.py 默认只绑 127.0.0.1，局域网/Cloudflare
    # 回源都会连不上。参数顺序按约定写成 `--host 0.0.0.0 --port 8940`。
    host_opt = "--host 0.0.0.0 " if host_supported else ""
    warn = "" if host_supported else (
        "# ⚠ 这个 server.py 不支持 --host：只能绑 127.0.0.1，\n"
        "#   局域网直连 http://192.168.5.41:8940/ 将失败（Cloudflare Tunnel 若在本机则无妨）。\n")
    return f"""# phix 官网 · systemd 单元（生成物；由网站仓库的 deploy.py 写入，可重跑覆盖）
# 位置：{UNIT_PATH}
#
# 单端口设计：{SITE_PORT} 上同时提供官网页面与 phix API 透传
#   /api/* 与 /healthz → {PHIX_SERVER}（字节透传，见 server.py 的 _proxy_to_phix）
#   其余路径          → 官网静态页
# 这样 Cloudflare 只映射这一个端口就够；8931 继续只对本机可见。
#
{warn}# 不动 phix.service：本单元只 After=（排序），不 Wants/Requires，重启官网不会连带动到 API 服务。

[Unit]
Description=phix 官网（{SITE_PORT}：官网页面 + /api/v1 透传到 8931）
After=network-online.target phix.service
Wants=network-online.target

[Service]
Type=simple
User=phix
Group=phix
WorkingDirectory={REMOTE_ROOT}
Environment=PHIX_SERVER={PHIX_SERVER}
ExecStart={VENV_PY} {REMOTE_ROOT}/server.py {host_opt}--port {SITE_PORT}
Restart=always
RestartSec=3
KillSignal=SIGTERM
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
"""


def do_upload(r: Remote, files: list[tuple[str, Path, int]]) -> dict:
    print(f"\n===== 上传 {len(files)} 个文件 → {REMOTE_ROOT} =====")
    r.mkdirs("")
    up_n = up_b = sk_n = sk_b = 0
    skipped_big: list[str] = []
    changed: list[str] = []          # 这一轮真的换了内容的文件（决定要不要重启）
    t0 = time.time()
    for i, (rel, path, size) in enumerate(files, 1):
        rpath = posixpath.join(REMOTE_ROOT, rel)
        r.mkdirs(posixpath.dirname(rel))
        rsize = r.size(rpath)
        if rsize == size:
            if size > HASH_LIMIT:
                print(f"[{i}/{len(files)}] 跳过（远端大小一致 {human(size)}）  {rel}", flush=True)
                sk_n += 1
                sk_b += size
                skipped_big.append(rel)
                continue
            if r.sha256(rpath, size) == sha256_file(path):
                print(f"[{i}/{len(files)}] 跳过（内容一致）  {rel}", flush=True)
                sk_n += 1
                sk_b += size
                continue
            print(f"[{i}/{len(files)}] 更新（大小相同但内容变了）  {rel}", flush=True)
        else:
            note = f"（远端 {human(rsize)} → 覆盖）" if rsize is not None else "（新文件）"
            print(f"[{i}/{len(files)}] 上传 {rel}  {human(size)} {note}", flush=True)
        r.put(path, rpath, size, rel)
        up_n += 1
        up_b += size
        changed.append(rel)
    return {"up_n": up_n, "up_b": up_b, "sk_n": sk_n, "sk_b": sk_b,
            "skipped_big": skipped_big, "secs": time.time() - t0,
            "changed": changed}


def do_install_unit(r: Remote, force_restart: bool = False) -> None:
    print(f"\n===== systemd 单元 {UNIT_NAME} =====")
    server_py = (LOCAL_DIR / "server.py").read_text(encoding="utf-8", errors="replace")
    host_supported = '"--host"' in server_py or "'--host'" in server_py
    if not host_supported:
        print("⚠ 本地 server.py 未见 --host 参数：将只绑 127.0.0.1，本机直连会失败。")
    unit = build_unit(host_supported)

    cur = r.sh(f"cat {UNIT_PATH} 2>/dev/null || true").strip()
    active = r.sh(f"systemctl is-active {UNIT_NAME} 2>/dev/null || true").strip()
    if cur == unit.strip() and active == "active":
        # 2026-09-13 修：老写法在这里**无条件 return**，于是"只改了 server.py / 页面"
        # 的部署**上传完文件却不重启进程** —— 文件是新的、跑的还是旧代码
        # （本次实测踩到：SSO 新端点已经上传，线上仍然 404）。
        # 现在：只要这一轮真有文件内容变化，就重启一次让新代码生效。
        if not force_restart:
            print("单元内容一致、服务在跑，且**这一轮没有任何文件变化** → 幂等跳过写入与重启")
            return
        print("单元内容一致，但**这一轮上传了有变化的文件** → 重启服务，让新代码真正生效")
        print(r.sudo(f"systemctl restart {UNIT_NAME}\n"
                     f"sleep 2\n"
                     f"echo '--- is-active ---'; systemctl is-active {UNIT_NAME} || true\n"
                     f"echo '--- 重启后是否真的换了代码（server.py mtime）---'; "
                     f"stat -c '%y %n' {REMOTE_ROOT}/server.py || true\n"
                     f"echo '--- phix.service（应仍为 active）---'; "
                     f"systemctl is-active phix.service || true"))
        return

    with r.sftp.open("/tmp/phix-site.service", "wb") as f:
        f.write(unit.encode("utf-8"))
    out = r.sudo(
        f"set -e\n"
        f"install -m 644 /tmp/phix-site.service {UNIT_PATH}\n"
        f"rm -f /tmp/phix-site.service\n"
        f"systemctl daemon-reload\n"
        f"systemctl enable --now {UNIT_NAME} >/dev/null 2>&1\n"
        f"systemctl restart {UNIT_NAME}\n"
        f"sleep 2\n"
        f"echo '--- is-active ---'; systemctl is-active {UNIT_NAME} || true\n"
        f"echo '--- is-enabled ---'; systemctl is-enabled {UNIT_NAME} || true\n"
        f"echo '--- phix.service（应仍为 active，全程没动它）---'; systemctl is-active phix.service || true\n"
    )
    print(out.strip())


def remote_checks(r: Remote) -> None:
    print("\n===== 远端自检（在 .41 上执行）=====")
    script = f"""
URL=http://127.0.0.1:{SITE_PORT}
if command -v curl >/dev/null 2>&1; then
  echo "--- curl -s -o /dev/null -w '%{{http_code}}' $URL/ ---"
  curl -s -o /dev/null -w '%{{http_code}}\\n' $URL/
  echo "--- curl -s $URL/api/v1/ping | head -c 200 ---"
  curl -s $URL/api/v1/ping | head -c 200; echo
  echo "--- curl -s -o /dev/null -w '%{{http_code}}' $URL/healthz ---"
  curl -s -o /dev/null -w '%{{http_code}}\\n' $URL/healthz
else
  echo "(远端未安装 curl —— Ubuntu 24.04 最小安装默认没有；改用 venv python 的 urllib 做等价检查)"
  echo "--- urllib GET $URL/ ---"
  {VENV_PY} -c 'import urllib.request as u; r=u.urlopen("http://127.0.0.1:{SITE_PORT}/",timeout=10); b=r.read(); print("HTTP", r.status, "| bytes:", len(b))'
  echo "--- urllib GET $URL/api/v1/ping (前 200 字节) ---"
  {VENV_PY} -c 'import urllib.request as u; print(u.urlopen("http://127.0.0.1:{SITE_PORT}/api/v1/ping",timeout=10).read(200).decode())'
  echo "--- urllib GET $URL/healthz ---"
  {VENV_PY} -c 'import urllib.request as u; r=u.urlopen("http://127.0.0.1:{SITE_PORT}/healthz",timeout=10); print("HTTP", r.status, "|", r.read(120).decode())'
fi
echo "--- systemctl is-active {UNIT_NAME} ---"
systemctl is-active {UNIT_NAME} || true
echo "--- systemctl is-enabled {UNIT_NAME} ---"
systemctl is-enabled {UNIT_NAME} || true
echo "--- journalctl -u {UNIT_NAME} -n 20 --no-pager ---"
echo 000000 | sudo -S -p '' journalctl -u {UNIT_NAME} -n 20 --no-pager 2>&1 | tail -25
echo "--- 监听地址（8940）---"
ss -ltnp 2>/dev/null | grep -E ':(8931|8940)' || echo '(没看到 8940)'
echo "--- 监听地址断言：8940 必须不是 127.0.0.1 ---"
if ss -ltn 2>/dev/null | awk '{{print $4}}' | grep -q ':8940$' && \\
   ! ss -ltn 2>/dev/null | awk '{{print $4}}' | grep -q '^127\\.0\\.0\\.1:8940$'; then
  echo "OK：8940 绑在非回环地址上"
else
  echo "✗ 8940 仍绑在 127.0.0.1！--host 没生效：检查单元 ExecStart 后 systemctl daemon-reload && restart"
fi
echo "--- 从局域网地址访问（在 .41 上打自己的 LAN IP，不是 127.0.0.1）---"
python3 -c 'import urllib.request;print(urllib.request.urlopen("http://{HOST}:{SITE_PORT}/",timeout=8).status)'
python3 -c 'import urllib.request;print(urllib.request.urlopen("http://{HOST}:{SITE_PORT}/api/v1/ping",timeout=8).read(200).decode())'
python3 -c 'import urllib.request;print(urllib.request.urlopen("http://{HOST}:{SITE_PORT}/healthz",timeout=8).read(120).decode())'
echo "--- phix.service 状态（应 active，未被本脚本动过）---"
systemctl is-active phix.service || true
echo "--- 远端 .site_secret / .phix_pubkey（本机自建，不接受上传）---"
ls -l {REMOTE_ROOT}/.site_secret {REMOTE_ROOT}/.phix_pubkey 2>&1
"""
    print(r.sh(script, timeout=180).strip())


# 安装包下载自检：HEAD 比大小（server.py 明确 Accept-Ranges: none，别真下 760MB），
# 再真下一个最小的包验 md5 —— 证明 8940 上的 /media/downloads/ 是逐字节完整的。
DOWNLOAD_PROBE = "xinlv-android.apk"

DOWNLOAD_TEMPLATE = r"""
echo "--- /media/downloads/ 经 LAN 8940 的 HEAD 大小比对（不下载整包）---"
python3 - <<'PY'
import urllib.request
BASE = "http://__HOST__:__PORT__/media/downloads/"
expect = {
__BODY__
}
bad = 0
for name, size in expect.items():
    try:
        req = urllib.request.Request(BASE + name, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            n = int(r.headers.get("Content-Length") or -1)
        ok = (r.status == 200 and n == size)
        bad += 0 if ok else 1
        print(f"  {'OK ' if ok else 'BAD'}  HTTP {r.status}  {n:>10} / {size:>10} 字节  {name}")
    except Exception as exc:
        bad += 1
        print(f"  ERR  {name}: {exc!r}")
print("大小不一致/失败的包数：", bad, "（应为 0）")
PY
echo "--- 真下一遍最小的包（__PROBE__）并算 md5 ---"
python3 - <<'PY'
import hashlib, urllib.request
url = "http://__HOST__:__PORT__/media/downloads/__PROBE__"
data = urllib.request.urlopen(url, timeout=120).read()
print("  下载字节:", len(data), " md5:", hashlib.md5(data).hexdigest())
PY
echo "--- 远端磁盘上的同文件 md5（用于逐字节比对）---"
md5sum __REMOTE_ROOT__/media/downloads/__PROBE__
echo "--- media/downloads 文件数与总字节 ---"
find __REMOTE_ROOT__/media/downloads -type f | wc -l
du -sb __REMOTE_ROOT__/media/downloads
"""


def download_checks(r: Remote) -> None:
    print("\n===== 安装包下载自检（/media/downloads/ 经 LAN）=====")
    dl_dir = LOCAL_DIR / "media" / "downloads"
    local = sorted(p for p in dl_dir.iterdir() if p.is_file())
    body = "\n".join(f'    "{p.name}": {p.stat().st_size},' for p in local)
    probe = DOWNLOAD_PROBE if (dl_dir / DOWNLOAD_PROBE).exists() else local[0].name
    local_md5 = hashlib.md5((dl_dir / probe).read_bytes()).hexdigest()
    script = (DOWNLOAD_TEMPLATE
              .replace("__HOST__", HOST).replace("__PORT__", str(SITE_PORT))
              .replace("__REMOTE_ROOT__", REMOTE_ROOT)
              .replace("__BODY__", body).replace("__PROBE__", probe))
    print(r.sh(script, timeout=300).strip())
    print(f"  本地同文件 md5（{probe}）:", local_md5)


def local_check() -> None:
    print("\n===== 本机（Windows）直连验证 =====")
    url = f"http://{HOST}:{SITE_PORT}/"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read()
        print(f"urllib GET {url} → HTTP {resp.status}，{len(body)} 字节")
    except Exception as exc:  # noqa: BLE001
        print(f"urllib GET {url} → 失败：{exc!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="幂等部署 phix 官网到 192.168.5.41")
    ap.add_argument("--skip-upload", action="store_true", help="跳过上传，只装单元并自检")
    ap.add_argument("--verify-only", action="store_true", help="只做远端/本机自检，不改任何东西")
    ap.add_argument("--host-flag", action="store_true",
                    help="强制在上传的 server.py 不支持时也加 --host（一般不用）")
    args = ap.parse_args()

    files, excluded = collect_files()
    total = sum(s for _, _, s in files)
    print(f"本地源：{LOCAL_DIR}")
    print(f"待传 {len(files)} 个文件，共 {human(total)}；排除 {len(excluded)} 项：")
    for e in excluded:
        print(f"  - {e}")

    r = Remote()
    try:
        if not args.verify_only:
            changed: list[str] = []
            if not args.skip_upload:
                stats = do_upload(r, files)
                changed = stats.get("changed", [])
                print(f"\n上传完成：新传/更新 {stats['up_n']} 个（{human(stats['up_b'])}），"
                      f"跳过 {stats['sk_n']} 个（{human(stats['sk_b'])}），"
                      f"耗时 {stats['secs']:.0f}s")
                if stats["skipped_big"]:
                    print(f"其中按大小跳过的大文件 {len(stats['skipped_big'])} 个："
                          + "、".join(stats["skipped_big"]))
                if changed:
                    print("本轮内容有变化的文件：" + "、".join(changed[:20])
                          + ("…" if len(changed) > 20 else ""))
            do_install_unit(r, force_restart=bool(changed))
        remote_checks(r)
        download_checks(r)
    finally:
        r.close()
    if not args.verify_only:
        local_check()
    print("\n完成。phix.service / ~/.config/phix/env / db.sqlite3 全程未动。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
