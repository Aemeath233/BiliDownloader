# 第三方组件

本项目自身的源码采用 MIT 许可证，见 [LICENSE](LICENSE)。第三方程序和 Python 包保留各自的许可证。

## FFmpeg

源码仓库不包含 FFmpeg 二进制或压缩包。源码用户自行安装 FFmpeg 并配置 PATH。

便携版通过单独的 `ffmpeg.exe` 子进程完成音视频封装，不将 FFmpeg 库链接进 Python 程序。当前发布构建使用 BtbN 的 `win64-gpl-shared` 包：

- 版本：`N-124223-gf044c5e627-20260428`
- FFmpeg 源码提交：[`f044c5e62710a151e001ae097fd3a07b88540766`](https://github.com/FFmpeg/FFmpeg/commit/f044c5e62710a151e001ae097fd3a07b88540766)
- 上游二进制和构建脚本：[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)
- 该 GPL 构建的许可证随程序内置于 `ffmpeg/LICENSE.txt`，同时作为 Release 附件提供。
- Release 提供对应的 FFmpeg 源码快照和 `FFmpeg-build-info.txt`，保留版本与编译配置。

这份 GPL 许可证适用于相应的 FFmpeg 构建及其受 GPL 约束的依赖，并不将第三方组件重新授权为 MIT。更多信息见 [FFmpeg 官方许可说明](https://www.ffmpeg.org/legal.html)；上游依赖构建配方见 [scripts.d](https://github.com/BtbN/FFmpeg-Builds/tree/master/scripts.d)。

## Python 组件

Python、CustomTkinter、HTTPX、certifi、Pillow、PyCryptodome、qrcode 和构建工具使用各自上游许可证。版本范围分别记录在 `requirements.txt`、`requirements-build.txt` 和 `requirements-dev.txt`。

项目不会把开发机的 Python 环境、账号文件、日志或下载视频放入 Git。便携版包含执行程序所需的运行组件，接收方无需安装 Python 或这些依赖。
