# 构建与发布

本文对应已发布的 Python/WebView2 v2.2.0。C# / WinUI 3 预览开发版请使用 [原生客户端构建与验证说明](WINUI.md)。

源码运行不需要 PyInstaller，也不需要仓库携带 FFmpeg。以下内容只面向生成发行程序的开发者。

## 开发工具

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check bili_client scripts tests main.py
.\.venv\Scripts\python.exe -m ruff format --check bili_client scripts tests main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt --progress-spinner off
```

CI 在 Windows 的 Python 3.11 和 3.12 下执行检查，GitHub Actions 固定到具体上游提交，权限只读。

## 安装版（默认交付）

准备下述精简 FFmpeg 及 Inno Setup 6 后执行：

```powershell
./scripts/build_installer.ps1
# 非标准编译器位置可显式传入：
./scripts/build_installer.ps1 -InnoCompiler 'C:/Tools/Inno Setup 6/ISCC.exe'
uv run --no-project --python 3.11 --with-requirements requirements-build.txt python scripts/verify_installer.py
uv run --no-project --python 3.11 python scripts/verify_ffmpeg_minimal.py
```

输出为 `dist/installer/BiliDownloader-Setup.exe`，构建超过 50 MiB 时失败。PyInstaller 使用文件夹模式，安装时展开一次，之后从固定目录启动，不再每次释放依赖到临时目录。`dist/installer/app/BiliDownloader` 是安装源目录，不是需要另发给用户的文件。

安装向导支持简体中文，默认当前用户安装，无需管理员权限；提供桌面和开始菜单快捷方式、系统卸载入口，沿用原有 AppId，版本与项目保持一致。覆盖升级仅清理旧 `_internal` 程序依赖，不删除账号、配置或下载目录；卸载保留用户文件。检测到客户端运行时要求先关闭，不强制结束下载。

构建脚本下载小型 Evergreen 引导程序，验证微软签名后随 Setup 携带。安装器按[微软说明](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)检查系统/用户、32/64 位注册表中的运行时版本；已有时跳过，缺少时在准备页面提示联网，并在写入应用文件前执行 `/silent /install`。失败则停止并提示重试。共享 WebView2 不随应用卸载。不安装固定版浏览器运行时，不进入浏览器页面。

验证脚本拒绝覆盖已有 BiliDownloader 安装及快捷方式，使用临时安装目录和独立账号配置。真实执行安装、三次应用启动、包内 FFmpeg 合并、覆盖升级及卸载，并校验合成用户数据/下载文件保留；只测试已有系统运行时路径，尚未在无 WebView2 的干净虚拟机测试联网补齐流程。报告位于 `build/installer-verification.json`。

## 准备发行版的精简 FFmpeg

默认发行包不携带 WebView2，也不携带通用转码工具。FFmpeg 只负责本地文件无损合并成 MP4；下载仍由 Python 完成。准备 Git Bash（或 MSYS2）、MSYS GNU make 和 Windows x64 MinGW GCC 后，从 FFmpeg 官方仓库下载固定源码：

```powershell
New-Item -ItemType Directory -Force build
Invoke-WebRequest 'https://api.github.com/repos/FFmpeg/FFmpeg/tarball/f044c5e62710a151e001ae097fd3a07b88540766' -OutFile build/FFmpeg-source-f044c5e627.tar.gz
uv run --no-project --python 3.11 python scripts/build_ffmpeg_minimal.py --source build/FFmpeg-source-f044c5e627.tar.gz --bash 'C:/Program Files/Git/bin/bash.exe' --make C:/msys64/usr/bin/make.exe --toolchain-dir C:/msys64/mingw64/bin
```

源码 SHA256 固定为 `842f845c8e7e5d338b60c3eeaeeefc0dc2da15401a7d12fb8a52dd56af2181df`；脚本拒绝不同档案。实际工具路径按开发机调整，GNU make 必须是 MSYS 版本，不能用 `mingw32-make`。Git Bash 与 make 应使用兼容的 MSYS 运行库。脚本不安装系统软件、不覆盖已有 `vendor/ffmpeg-minimal`。输出包含独立静态 `bin/ffmpeg.exe`、LGPL 许可证、完整编译参数和对应未修改源码。

构建保留 H.264 / HEVC / AV1、AAC / E-AC-3 / FLAC 的相关解析器、MOV/MP4/FLV 等解封装及 MP4 封装，不带转码编码器或网络协议。生成包内的 FFmpeg 不用于素材生成或任意格式转码。

需要替换 FFmpeg 时，将自己的兼容构建放到 `vendor/ffmpeg-minimal/bin/ffmpeg.exe`，保留许可证、`BUILD-INFO.txt`、`SOURCE.json` 和对应源码附件，再重新打包。使用者也可以直接在设置中选择自己的外部 FFmpeg，无需改动源码。

### 开发测试使用的全功能 FFmpeg

开发测试素材使用 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html)所列的 [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases) `ffmpeg-master-latest-win64-gpl-shared.zip`。将相应 ZIP 放在项目根目录后运行：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_ffmpeg.py
```

资源放在被 Git 忽略的 `vendor/ffmpeg`。全功能包默认只供开发验证生成测试素材；仅显式指定 `scripts/build_portable.ps1 -FullFFmpeg` 才将它放入发行包。其 GPL 许可证与对应源码须随分发保留，共享构建 DLL 保持与 ffmpeg.exe 同目录。其他 FFmpeg 构建需要自行确保依赖与许可证完整。

## 单文件便携版

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_portable.ps1
# 同时构建完整文件夹 ZIP：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_portable.ps1 -IncludeFolder
```

脚本优先使用 uv 与 Python 3.11；没有 uv 时使用 `.venv` 或 PATH 中的 Python，请预先安装 `requirements-build.txt`。输出：

```text
dist/portable/BiliDownloader-Portable.exe
```

标准构建只分发这个 exe，用户不需要安装 Python 或 FFmpeg，但电脑需已有 Microsoft WebView2 Runtime。程序启动时释放依赖到临时目录，正常退出时清理；设置和账号存放在当前用户的 APPDATA 中。这个“便携”指免安装分发，不是把账号随 exe 在电脑间迁移。

`-IncludeFolder` 还会输出 `dist/portable/folder/BiliDownloader/` 和 `dist/portable/BiliDownloader-Folder-Portable.zip`，后者完整解压即可使用，启动无需每次释放运行库。

## 自带 WebView2 的完整便携版

需要兼容没有 WebView2 的电脑时，携带微软 Fixed Version Runtime，程序通过 `WEBVIEW2_RUNTIME_PATH` 使用包内版本，不安装到系统、不请求管理员权限、不在用户启动时下载组件。

```powershell
uv run --no-project --python 3.11 --with-requirements requirements-build.txt python scripts/prepare_webview2.py
./scripts/build_portable.ps1 -IncludeWebView2 -IncludeFolder
```

准备脚本从[微软官方下载页](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)选择 x64 Fixed Version CAB，提取前保留 CAB SHA256，验证 `msedgewebview2.exe` 的微软签名后写入 `vendor/webview2/`。已存在的运行时不会自动替换。若网页结构变化，也可从同一页面手动下载并用 `expand.exe -F:*` 提取，将包含 `msedgewebview2.exe` 的整个目录保存到 `vendor/webview2/`，保留所有运行时文件。运行时和 CAB 均被 Git 忽略。

完整构建输出到 `dist/self-contained/`，文件夹版位于其 `folder/BiliDownloader/` 下。它比标准版更大，单文件每次启动需要解包整个浏览器运行时；常用建议选择安装版。固定版本不会自动更新，发布新版时应定期重新准备微软运行时。

目标系统为已更新的 Windows 10/11 x64（含 .NET Framework 4.6.2 或更新版本）。免安装不表示不写任何文件，账号保护和数据目录继续使用原有 Windows 用户配置。

## 文件夹版与安装包

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm main.spec
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_installer.ps1
```

单独运行 `main.spec` 的文件夹版输出到 `dist/BiliDownloader`，必须保留 `_internal`。安装版构建流程和目录见上文；不递归搜索磁盘。

## 验证单文件

关闭正在运行的 BiliDownloader，再执行：

```powershell
.\.venv\Scripts\python.exe scripts/verify_portable.py
.\.venv\Scripts\python.exe scripts/verify_portable.py --folder
# 完整便携版：
.\.venv\Scripts\python.exe scripts/verify_portable.py --self-contained
.\.venv\Scripts\python.exe scripts/verify_portable.py --self-contained --folder
# 实际内嵌界面流程，使用隔离演示服务，不登录或下载真实内容：
.\.venv\Scripts\python.exe scripts/verify_webview.py --screenshots
```

验证工具使用独立临时账号目录，复制一个 exe、移除 Python 环境变量并限制 PATH，验证 WebView2 界面与 Python 的就绪握手、资源/DLL、真实 FFmpeg 合并和正常退出清理。报告位于 `build/portable-verification.json`，不写入用户现有设置。`verify_webview.py` 导出解析页、队列和深色设置页的真实渲染 PNG，并检查分页选择、设置、搜索和快捷键。

## 发布边界

Git 只包含源码、测试、文档、图标和构建配置。编译程序、FFmpeg、压缩包、环境、下载视频和账号数据均不入库。默认安装包通过 GitHub Release 提供，附校验值、许可证、FFmpeg 来源与对应源码信息。发布前重新构建并验证，避免源码与 Release 程序不同步。
