# 构建矩阵（构建代理照此执行；产物统一拷到 `D:\phix\website\media\downloads\`）

> 约定文件名见 CONTRACT §6。**只构建、不改业务代码**；构建失败就如实回报失败原因，不要改源码去迁就构建。
> 每个产物构建前先确认对应仓库的工作区改动是"本轮引导/头像"那批（`git status` 看一眼，别把无关脏文件带进去）。

| 产物 | 命令（在该仓库根目录） | 输出 → 目标文件名 |
|---|---|---|
| 心履 Windows | `cd D:\moodsite && flutter build windows --release` | `build\windows\...\*.zip` 打包 → `xinlv-windows.zip` |
| 心履 macOS | `flutter build macos --release`（本机若无 mac 工具链：**跳过并回报**，不要伪造） | `xinlv-macos.zip` |
| 心履 Android | `flutter build apk --release` | `xinlv-android.apk` |
| PHL Windows | `cd D:\phl-dev\PH-Launcher && npm ci && npm run dist:win`（若无该 script 则 `npx electron-builder --win`） | `phl-windows-setup.exe` |
| PHL macOS | `npx electron-builder --mac`（无证书则 `--dir` 产出后 zip 并回报"未签名"） | `phl-macos.zip` |
| PLL Windows | `cd D:\phl-lite-dev && python -m PyInstaller --noconfirm --clean PingheLauncherLite.spec`，再 `cd installer && ..\tools\wix314\candle.exe PingheLauncherLite.wxs -nologo && ..\tools\wix314\light.exe PingheLauncherLite.wixobj -out PingheLauncherLite.msi -nologo`（**不要加 `-ext WixUIExtension`**，会把数据库代码页压回 1252、中文报 LGHT0311） | `phllite-windows-setup.msi`（**只出安装版，不出便携版 exe**） |
| PLL macOS | `cd D:\phl-lite-dev && python -X utf8 scripts\macos_build.py --host 192.168.5.13 --user huazixian`（在局域网 Mac 上构建后自动拉回 `deliver/`） | `phllite-macos.dmg` |
| 官网 + PHL 网页版 | 无需构建；`D:\phix\server\.venv\Scripts\python.exe D:\phix\website\server.py --port 8940` 直接跑 | — |

注意：
- flutter / electron-builder / PyInstaller 若本机缺工具链，**如实回报缺什么**，不要装大型 SDK 除非一条命令能装好。
- 构建日志写到 `D:\phix\_lab\build_<name>.log`。
- 拷产物时用**复制**，不要移动；源目录保持原样。
