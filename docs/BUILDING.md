# 构建与发布

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

## 准备发行版的 FFmpeg

从 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html) 选择 Windows 构建。当前便携发行版采用 [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases) 的 `ffmpeg-master-latest-win64-gpl-shared.zip`。将相应 ZIP 放在项目根目录后运行：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_ffmpeg.py
```

资源放在被 Git 忽略的 `vendor/ffmpeg`。便携和安装构建要求同时具备 `bin/ffmpeg.exe` 和 `LICENSE.txt`；DLL 保持与 ffmpeg.exe 同目录。构建脚本不会搜索其他磁盘来补齐资源。其他 FFmpeg 构建需要自行确保依赖与许可证完整。

## 单文件便携版

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_portable.ps1
```

脚本优先使用 uv 与 Python 3.11；没有 uv 时使用 `.venv` 或 PATH 中的 Python，请预先安装 `requirements-build.txt`。输出：

```text
dist/portable/BiliDownloader-Portable.exe
```

只分发这个 exe，用户不需要安装 Python 或 FFmpeg。程序启动时释放依赖到临时目录，正常退出时清理；设置和账号存放在当前用户的 APPDATA 中。这个“便携”指免安装单文件分发，不是把账号随 exe 在电脑间迁移。

## 文件夹版与安装包

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm main.spec
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_installer.ps1
```

文件夹版输出到 `dist/BiliDownloader`，必须保留 `_internal`。安装包还需要安装 Inno Setup 6，将 `ISCC.exe` 加入 PATH，或使用它的标准安装目录；脚本不递归搜索磁盘。

## 验证单文件

关闭正在运行的 BiliDownloader，再执行：

```powershell
.\.venv\Scripts\python.exe scripts/verify_portable.py
```

验证工具使用独立临时账号目录，复制一个 exe、移除 Python 环境变量并限制 PATH，验证启动、DLL、真实 FFmpeg 合并和正常退出清理。报告位于 `build/portable-verification.json`，不写入用户现有设置。

## 发布边界

Git 只包含源码、测试、文档、图标和构建配置。编译程序、FFmpeg、压缩包、环境、下载视频和账号数据均不入库。便携 exe 通过 GitHub Release 提供，附校验值、许可证、FFmpeg 来源与对应源码信息。发布前重新构建并验证，避免源码与 Release 程序不同步。
