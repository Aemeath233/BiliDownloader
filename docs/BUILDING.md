# 安装包与 FFmpeg 构建

当前源码为 C# / WinUI 3 客户端。运行与完整测试入口见 [WinUI 开发说明](WINUI.md)，源码组织见[架构说明](ARCHITECTURE.md)。

## 开发环境

- Windows x64、PowerShell 7、.NET 10 SDK、Windows SDK 10.0.26100。
- Inno Setup 6 用于生成安装包。
- FFmpeg 用于封装，开发测试另需 ffprobe 和具备 H.264/AAC 编码器的全功能 FFmpeg。

## 原生客户端与安装包

在仓库根目录执行。

```powershell
dotnet build BiliDownloader.sln -c Release -p:Platform=x64
dotnet publish src/BiliDownloader.WinUI/BiliDownloader.WinUI.csproj -c Release -p:Platform=x64 -p:UiRegression=false -o dist/winui
.\scripts\build_native_installer.ps1
# 可显式选择 Inno Setup 编译器
.\scripts\build_native_installer.ps1 -InnoCompiler 'C:\Tools\Inno Setup 6\ISCC.exe'
```

安装包构建要求 `vendor/ffmpeg-minimal` 中有 `bin/ffmpeg.exe`、`LICENSE.txt`、`BUILD-INFO.txt`、`SOURCE.json` 和对应源码 `FFmpeg-source-f044c5e627.tar.gz`。构建使用独立程序目录，生成资源完整的自包含发布结果，再汇集组件许可、清单和 SHA256。

安装包输出为 `dist/native-installer/BiliDownloader-Setup.exe`，构建上限为 100 MiB。该目录同时包含构建信息、许可压缩包、FFmpeg 来源与对应源码，以及 `SHA256SUMS.txt`。运行时包含 .NET、WinUI 与精简 FFmpeg。

## 准备发行版的精简 FFmpeg

发行包通过独立进程调用静态 FFmpeg，负责媒体流封装。下载由 C# 核心完成。准备 Git Bash 或 MSYS2、MSYS GNU make、Windows x64 MinGW GCC 后执行。

```powershell
New-Item -ItemType Directory -Path build -Force
Invoke-WebRequest 'https://api.github.com/repos/FFmpeg/FFmpeg/tarball/f044c5e62710a151e001ae097fd3a07b88540766' -OutFile build/FFmpeg-source-f044c5e627.tar.gz
.\scripts\build_ffmpeg_minimal.ps1 -Source build/FFmpeg-source-f044c5e627.tar.gz -Bash 'C:\Program Files\Git\bin\bash.exe' -Make 'C:\msys64\usr\bin\make.exe' -ToolchainDirectory 'C:\msys64\mingw64\bin'
```

源码固定为提交 `f044c5e62710a151e001ae097fd3a07b88540766`，压缩包 SHA256 为 `842f845c8e7e5d338b60c3eeaeeefc0dc2da15401a7d12fb8a52dd56af2181df`。脚本在提取前校验哈希，使用独立工作目录，并拒绝覆盖已有 `vendor/ffmpeg-minimal`。重建前请将原目录移到其他位置。

GNU make 使用 MSYS 版本，Git Bash 与 make 需要兼容的 MSYS 运行库。按实际工具位置调整参数。构建保留 H.264 / HEVC / AV1、AAC / E-AC-3 / FLAC 的相关解析器、媒体解封装与 MP4 封装。输出包含静态程序、LGPL 许可证、编译信息和未经修改的对应源码。

若使用外部 FFmpeg，可在应用设置中选择其路径。替换发行包的 FFmpeg 时，需要同步保留许可证、构建信息、来源和对应源码。第三方分发要求见[组件说明](../THIRD_PARTY_NOTICES.md)。

## 准备开发测试工具

从 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html)所列的 [BtbN 构建](https://github.com/BtbN/FFmpeg-Builds/releases)获取 Windows x64 的共享 ZIP，确认包含 FFmpeg、ffprobe、DLL 和许可证后运行。

```powershell
.\scripts\prepare_ffmpeg.ps1 -Archive 'C:\Downloads\ffmpeg-master-latest-win64-gpl-shared.zip'
```

工具输出到 `vendor/ffmpeg`，保留共享 DLL 与许可证。该目录用于生成测试素材和验证音轨、编码包哈希。脚本拒绝覆盖已有目录。开发用 GPL 构建只用于测试，安装包使用上面的精简构建。

## 验证与发布

准备好开发用 FFmpeg 和精简 FFmpeg 后执行。

```powershell
.\scripts\verify_native.ps1
.\scripts\verify_native_installer.ps1
```

完整检查包含核心回归、原生界面交互、生产发布启动退出和 NuGet 依赖扫描。安装验证使用独立 AppId、快捷方式、程序目录和用户数据，检查载荷哈希、覆盖升级、数据保留、运行保护、新安装和卸载。报告写入 `build` 的独立目录。

发布前从确认的 Git 提交重建，核对构建信息中的 `source_commit`，验证安装包后将 `dist/native-installer` 的附件上传到对应 Release。仓库保存源码、测试、资源与构建配置，依赖二进制和发布程序由本地资源目录与 Release 提供。
