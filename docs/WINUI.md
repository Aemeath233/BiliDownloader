# WinUI 3 开发与验证

当前原生客户端为 3.0.0，使用 C#、.NET 10、WinUI 3 和独立 FFmpeg 进程。[Windows x64 安装包](https://github.com/Aemeath233/BiliDownloader/releases/tag/v3.0.0) 包含程序所需的 .NET、WinUI 和精简 FFmpeg。安装包与 FFmpeg 准备方法见[构建说明](BUILDING.md)。

## 源码结构

| 位置 | 内容 |
| --- | --- |
| `BiliDownloader.sln` | 原生客户端解决方案 |
| `src/BiliDownloader.Core` | 网络边界、解析、登录、调度、限速、存储和 FFmpeg |
| `src/BiliDownloader.WinUI` | XAML 页面、原生窗口、列表展示和交互 |
| `tests/native` | 可执行核心回归程序，包含真实媒体封装检查 |
| `scripts/WinUIRegression.cs` | 仅测试构建编译的原生界面回归入口 |
| `scripts/WinUIExperienceAudit.cs` | 导航、压力场景、控件操作、时序追踪与截图 |
| `scripts/verify_native.ps1` | 完整原生检查入口 |
| `scripts/verify_winui.ps1` | UI 检查与完整交互场景 |
| `scripts/verify_publish.ps1` | 全新目录发布及生产 EXE 启动退出检查 |
| `scripts/build_native_installer.ps1` | 原生安装包、运行组件声明、构建清单及校验文件 |
| `scripts/verify_native_installer.ps1` | 独立安装标识下的升级、启动、封装、快捷方式和卸载检查 |

UI 在同一窗口中切换四个页面。解析与下载异步运行，切页保留解析结果和后台任务。下载核心负责任务状态，UI 定时读取进度和计数。文件完成封装后以避让同名文件的方式发布。

## 开发环境

- Windows x64 与 PowerShell 7。本轮在 Windows 11、150% 显示缩放下验证。
- .NET 10 SDK。本机验证版本为 10.0.403。
- Windows SDK 10.0.26100。使用 Visual Studio 开发时安装相应 Windows 应用开发组件。
- NuGet 依赖通过根目录 `NuGet.Config` 中的 nuget.org 还原。
- FFmpeg 用于封装，可在设置中指定绝对路径，也可从程序目录或 PATH 中的绝对目录查找。

当前项目目标为 `net10.0-windows10.0.26100.0`，声明的最低系统版本为 Windows 10 build 19041。最低系统版本的实际运行与安装仍需在对应设备验收。

## 构建与运行

在仓库根目录执行。

```powershell
dotnet build BiliDownloader.sln -c Release -p:Platform=x64
& '.\src\BiliDownloader.WinUI\bin\x64\Release\net10.0-windows10.0.26100.0\win-x64\BiliDownloader.exe'
```

程序默认使用 `%APPDATA%\BiliDownloader` 保存配置、登录信息、任务记录与日志。首次进入原生客户端会创建 `backup-before-winui3` 迁移备份，登录信息受当前 Windows 用户 DPAPI 保护。

开发时可以用独立目录验收。

```powershell
& '.\src\BiliDownloader.WinUI\bin\x64\Release\net10.0-windows10.0.26100.0\win-x64\BiliDownloader.exe' --profile "$PWD\build\manual-profile"
```

Ctrl+L 打开添加下载并聚焦链接输入框。开始下载后可以继续解析其他链接，任务在后台排队运行。

## 自包含发布

```powershell
dotnet publish .\src\BiliDownloader.WinUI\BiliDownloader.WinUI.csproj -c Release -p:Platform=x64 -p:UiRegression=false -o .\dist\winui
```

发布目录包含 .NET 和 WinUI 运行文件。运行与移动时保留整个目录。项目显式将应用 PRI 与编译 XAML 加入发布清单，确保正常构建和发布使用相同的应用资源。

如果 `vendor/ffmpeg-minimal/bin/ffmpeg.exe` 存在，会复制到程序目录。开发者也可以在设置中选择外部 FFmpeg。安装包构建会按 [第三方说明](../THIRD_PARTY_NOTICES.md) 汇集实际携带组件的许可证、FFmpeg 构建信息与对应源码。

## 安装包构建与验证

准备精简 FFmpeg 和 Inno Setup 6 后运行。

```powershell
.\scripts\build_native_installer.ps1
# 非标准编译器位置可以显式指定
.\scripts\build_native_installer.ps1 -InnoCompiler 'C:\Tools\Inno Setup 6\ISCC.exe'
.\scripts\verify_native_installer.ps1
```

输出位于 `dist/native-installer/`。安装包默认安装到 `%LOCALAPPDATA%\Programs\BiliDownloader`，提供桌面快捷方式、开始菜单入口和系统卸载入口。安装时要求先关闭客户端，覆盖升级清理旧 `_internal` 程序依赖，保留用户目录和已下载文件。

验证脚本以独立 AppId 与快捷方式名称编译同一套安装脚本，逐文件比对发行载荷。脚本先建立原生基线安装，再注入合成的旧依赖目录、用户文件和登录数据，检查升级时的依赖清理与数据迁移。随后验证三次启动、DPAPI 迁移、使用安装目录 FFmpeg 的核心回归、运行中阻止覆盖与卸载、覆盖升级、新安装和卸载。报告和日志保存在 `build/native-installer-verification`。

当前 Windows 安装流程在开发机以隔离安装标识验证。缺少开发运行环境的干净虚拟机与生产安装标识下的升级需继续验收。

## 完整自动化检查

媒体测试需要以下三个已准备的文件。

```text
vendor/ffmpeg/bin/ffmpeg.exe
vendor/ffmpeg/bin/ffprobe.exe
vendor/ffmpeg-minimal/bin/ffmpeg.exe
```

前两个用于生成真实 H.264/AAC 素材、检查输出音轨和编码包哈希，精简版本用于执行实际封装。准备方法见 [FFmpeg 开发构建说明](BUILDING.md#准备发行版的精简-ffmpeg)。开发用共享构建需保留同目录 DLL。

在已登录的 Windows 桌面会话中执行。

```powershell
.\scripts\verify_native.ps1
```

该入口运行核心测试、UI 完整场景、生产发布冒烟检查和 NuGet 已知漏洞扫描。测试使用独立 profile、下载目录和模拟 HTTP。UI 测试通过应用自身的控件、AutomationPeer 与调度接口驱动，保留过程追踪、时序数据和 XAML 截图。

只运行其中一部分时可以使用以下命令。

```powershell
.\scripts\verify_winui.ps1 -Experience
.\scripts\verify_publish.ps1
```

报告默认写入 `build` 下的独立目录，可用 `-ReportDirectory` 指定位置。成功后清理临时 UI 测试程序，失败时保留供定位。生产发布目录保留，便于人工验收。

GitHub Actions 的原生任务执行生产项目与测试代码编译、发布资源完整性检查及 NuGet 扫描。桌面交互测试与真实 FFmpeg 测试由上述完整本地脚本执行。

## 验收范围

本机已通过 23 项核心测试、26 组 UI 检查和发布版启动退出检查。详细场景、修复与响应耗时见 [审查报告](WINUI-AUDIT-2026-11-06.md)。实网扫码、会员权限、平台风控、其他 DPI、GPU 呈现帧率以及干净系统安装升级需要进一步验收。
