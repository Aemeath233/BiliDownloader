# 第三方组件

项目源码采用 [MIT 许可证](LICENSE)。发行包中的第三方程序与运行组件保留各自许可证。

## .NET 与 WinUI

原生项目通过 NuGet 引用 Windows App SDK WinUI 组件、Windows SDK BuildTools、System.Security.Cryptography.ProtectedData 和 QRCoder。版本与传递依赖以项目文件和还原结果为准。

- QRCoder 1.6.0 使用 MIT 许可证，来源为 [QRCoder](https://github.com/codebude/QRCoder)。
- .NET 与 Windows App SDK 组件按各自发行包的许可和第三方声明分发。Windows App SDK 的许可见 [上游项目](https://github.com/microsoft/WindowsAppSDK)。
- 安装包的 `licenses` 目录汇集实际发行组件的许可与通知，包括 .NET Runtime、Windows App SDK、QRCoder 和 FFmpeg。Release 同时提供 `Runtime-Licenses.zip`。

## FFmpeg

原生客户端通过独立 FFmpeg 进程封装媒体。发行包使用固定源码构建精简静态 FFmpeg，仅保留本地文件读取、相关解封装、解析器及 MP4 封装。

- 源码提交为 [`f044c5e62710a151e001ae097fd3a07b88540766`](https://github.com/FFmpeg/FFmpeg/commit/f044c5e62710a151e001ae097fd3a07b88540766)。
- 源码压缩包 SHA256 为 `842f845c8e7e5d338b60c3eeaeeefc0dc2da15401a7d12fb8a52dd56af2181df`。
- 构建脚本为 `scripts/build_ffmpeg_minimal.ps1`，完整配置与编译器信息记录在 `FFmpeg-source-info.json` 和 `FFmpeg-build-info.txt`。
- 本构建采用 LGPL 2.1 或更新版本，许可证随程序保存在 `licenses/ffmpeg/LICENSE.txt`，Release 附件同时提供许可证和未经修改的对应源码。

开发测试使用 BtbN 的全功能共享构建生成素材和检查输出。该测试工具位于 `vendor/ffmpeg`，保持 DLL 与许可证完整。替换发行包的 FFmpeg 时，应重新核对其许可、对应源码与依赖信息。详见 [FFmpeg 官方许可说明](https://www.ffmpeg.org/legal.html)及 [BtbN 构建配方](https://github.com/BtbN/FFmpeg-Builds/tree/master/scripts.d)。准备方式见[构建说明](docs/BUILDING.md)。

## 安装程序

安装包由 [Inno Setup](https://jrsoftware.org/) 生成。简体中文翻译来自其官方仓库的 [ChineseSimplified.isl](https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseSimplified.isl)，保留原文件的译者说明。

仓库保存应用源码、图标、测试和构建配置。开发环境、第三方二进制、用户账号、日志和下载文件由 Git 忽略。
