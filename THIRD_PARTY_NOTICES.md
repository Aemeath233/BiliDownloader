# 第三方组件

本项目自身的源码采用 MIT 许可证，见 [LICENSE](LICENSE)。第三方程序和 Python 包保留各自的许可证。

## FFmpeg

源码仓库不包含 FFmpeg 二进制或压缩包。源码用户自行安装 FFmpeg 并配置 PATH。

便携版通过单独的 `ffmpeg.exe` 子进程完成音视频封装，不将 FFmpeg 库链接进 Python 程序。默认构建从以下固定源码编译精简静态 FFmpeg，只启用本地文件读取、相关解封装、解析器及 MP4 封装；不含音视频编码器，不启用 GPL、nonfree 或外部媒体库：

- FFmpeg 源码提交：[`f044c5e62710a151e001ae097fd3a07b88540766`](https://github.com/FFmpeg/FFmpeg/commit/f044c5e62710a151e001ae097fd3a07b88540766)
- 源码压缩包 SHA256：`842f845c8e7e5d338b60c3eeaeeefc0dc2da15401a7d12fb8a52dd56af2181df`
- 本地编译脚本：`scripts/build_ffmpeg_minimal.py`，完整配置、编译器信息保存在 `FFmpeg-source-info.json` 和 `FFmpeg-build-info.txt`。
- 本构建采用 LGPL 2.1 或更新版本；许可证随程序内置于 `ffmpeg/LICENSE.txt`，同时作为分发附件提供。
- 分发目录保留未经修改的对应 FFmpeg 源码快照，作为单独附件；不计入用户运行所需的单个 EXE。替换 FFmpeg 后重新打包的方法见 `docs/BUILDING.md`。

开发验证另用 BtbN 的 `N-124223-gf044c5e627-20260428 win64-gpl-shared` 全功能包生成测试素材；它默认不进入发行程序。只有显式指定 `-FullFFmpeg` 才分发该 GPL 构建，必须随包保留其 GPL 许可证、对应源码及上游依赖构建信息。更多信息见 [FFmpeg 官方许可说明](https://www.ffmpeg.org/legal.html)及 [BtbN 构建配方](https://github.com/BtbN/FFmpeg-Builds/tree/master/scripts.d)。

## Python 组件

Python、pywebview、pythonnet、HTTPX、certifi、Pillow、PyCryptodome、qrcode 和构建工具使用各自上游许可证。CustomTkinter 仅供旧界面回归测试，不再进入发行包。版本范围分别记录在 `requirements.txt`、`requirements-build.txt` 和 `requirements-dev.txt`。

## Microsoft WebView2

默认安装包携带微软签名的 Evergreen 小型引导程序，已有运行时即跳过，缺少时由安装向导联网安装；校验值记录在开发资源目录的 `SOURCE.json`。应用卸载时保留共享运行时。

安装程序由 [Inno Setup](https://jrsoftware.org/) 生成；简体中文翻译来自其官方仓库的 [ChineseSimplified.isl](https://github.com/jrsoftware/issrc/blob/main/Files/Languages/ChineseSimplified.isl)，保留原文件的译者说明。

内嵌界面使用 Microsoft WebView2。标准版使用系统安装的运行时；完整便携版包含从[微软官方下载页](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)取得的 x64 Fixed Version Runtime，保留完整上游目录、第三方组件信息和签名文件。该运行时适用微软提供的许可条款，不重新授权为 MIT。来源、版本和 CAB 校验值保存在运行时目录的 `bili-runtime-source.json` 中。WebView2 SDK 互操作组件由 pywebview 包提供。

项目不会把开发机的 Python 环境、账号文件、日志或下载视频放入 Git。便携版包含执行程序所需的运行组件，接收方无需安装 Python 或这些依赖。
