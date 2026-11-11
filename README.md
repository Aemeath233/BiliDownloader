# BiliDownloader

[![Source checks](https://github.com/Aemeath233/BiliDownloader/actions/workflows/ci.yml/badge.svg)](https://github.com/Aemeath233/BiliDownloader/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> [!WARNING]
> **⚠️ 使用声明：本项目仅供学习与技术交流**
>
> 请勿用于商业运营、收费下载服务或其他牟利用途。请仅下载和保存你有权使用的内容，未经权利人许可，请勿转载、售卖或传播所下载的视频。
>
> 请尊重创作者权益，遵守 B 站的平台规则和适用法律法规。项目源码的公开不代表对任何视频内容的授权。
>
> 本声明为使用倡议；源码授权仍以 [MIT 许可证](LICENSE) 为准，不构成对该许可证新增的非商业限制。

## 当前版本 · v3.0.0-preview.1

BiliDownloader 是一个 Windows 桌面端 B 站视频下载工具。3.0 使用 C#、.NET 10 和 WinUI 3 实现解析、扫码登录、下载队列与原生界面，通过 FFmpeg 完成音视频封装。

**[下载 Windows x64 安装包](https://github.com/Aemeath233/BiliDownloader/releases/download/v3.0.0-preview.1/BiliDownloader-Setup.exe)** · [发行说明与校验文件](https://github.com/Aemeath233/BiliDownloader/releases/tag/v3.0.0-preview.1) · [更新日志](CHANGELOG.md)

当前版本为 3.0 预览版。GitHub 的 `Latest` 标记仍指向旧版 v2.2.0，请使用上面的 3.0 下载入口。旧版使用与开发说明已归档到 [v2.2.0 文档](docs/LEGACY-2.2.md)。

## 安装与开始使用

1. 在 Windows 10 / 11 x64 电脑上下载并运行 `BiliDownloader-Setup.exe`，按向导完成安装。
2. 从桌面或开始菜单启动，粘贴 B 站分享链接或 BV / ep / ss 编号，点击“解析”。需要账号权限时，点击“扫码登录”。
3. 选择分集、下载内容、画质和保存目录，点击“开始下载”。
4. 在“下载队列”查看进度。下载过程中可以切回“添加下载”继续解析其他链接并加入队列，完成后可打开文件或定位目录。

安装包约 45.5 MiB，包含 .NET、WinUI 运行文件与精简 FFmpeg。默认安装到 `%LOCALAPPDATA%\Programs\BiliDownloader`，无需管理员权限。支持覆盖升级、桌面与开始菜单快捷方式，以及 Windows 应用卸载入口。

升级保留账号、设置、任务记录与已下载文件。卸载保留用户数据和下载文件。项目声明的最低系统版本为 Windows 10 build 19041，最低版本设备及干净系统安装仍需进一步验收。

## 功能

- 支持单视频、多 P 合集、番剧，以及 `bilibili.com` 链接、`b23.tv` 短链和分享文本。
- 下载内容可选完整视频、仅音频或无声视频，设置中可调整默认方式。完整视频与无声视频保存为 MP4，仅音频保存为 M4A 并保留源编码。
- 分集支持全选、反选，长标题单行省略并在悬停时显示全文。支持画质记忆、刷新画质、命名模板和文件名预览。
- 可为本次下载更改保存目录，也可在设置中修改默认目录、按来源建立子文件夹。
- 队列支持状态筛选、标题与文件名搜索、跨页选择、批量取消 / 重试 / 移除，以及失败任务重选画质。
- 设置直接控制同时下载任务数与全局限速。任务在后台运行，切换页面保留解析结果与下载进度。
- 下载失败可切换备用媒体地址，保存文件时避让已有同名文件。重试从头下载。
- 原生 WinUI 控件与蓝白主题，支持浅色、深色、跟随系统和关闭界面动画。
- 兼容旧版用户数据，首次迁移创建备份，登录信息使用当前 Windows 用户的 DPAPI 保护。

## 使用说明

在链接输入框中按 `Enter` 解析，按 `Ctrl+L` 可返回“添加下载”并聚焦输入框。关闭窗口时如果仍有任务，可选择最小化继续下载或停止并退出。

下载队列中的“移除”和“清理已结束”用于清理任务记录。已完成的文件可通过“打开”或“定位”访问。

可用画质取决于账号、会员权限与平台返回的媒体流。程序按实际可获取的档位显示选项，批量下载时逐集校验。取流画质不符时任务停止，可返回重选画质。

## 数据与隐私

配置、账号、任务与日志保存在 `%APPDATA%\BiliDownloader`。

```text
config.json             全局设置
auth.json               当前 Windows 用户 DPAPI 保护的登录信息
tasks.json              下载任务记录
logs/                   运行日志
backup-before-winui3/   首次迁移到原生客户端时的备份
```

账号文件绑定当前 Windows 用户。请求使用 HTTPS 和证书校验，分享链接与短链跳转有域名限制，账号 Cookie 只用于 B 站域名，日志会隐藏常见账号令牌。

## 常见问题

**为什么没有 4K、高帧率或某些视频的下载选项？**

账号、会员、版权和平台接口决定可用内容。扫码登录后可刷新画质。遇到平台请求限制时请稍后重试。当前版本的重试从头下载，暂不支持断点续传、MP3 转码、DRM 内容和自动拼接多段 MP4。

**提示找不到 FFmpeg 怎么办？**

3.0 安装包已包含精简 FFmpeg。源码构建时需要自行准备，可在设置中选择 `ffmpeg.exe` 的完整路径，或将其目录加入 PATH。

**3.0 能使用之前的设置和任务记录吗？**

原生客户端读取原有用户数据格式，并在首次迁移时建立备份。正在运行但未完成的旧任务会恢复为可重试状态。

## 源码开发与验证

开发环境需要 Windows x64、.NET 10 SDK 和 Windows SDK 10.0.26100。从仓库根目录构建并运行。

```powershell
git clone https://github.com/Aemeath233/BiliDownloader.git
cd BiliDownloader
dotnet build BiliDownloader.sln -c Release -p:Platform=x64
& '.\src\BiliDownloader.WinUI\bin\x64\Release\net10.0-windows10.0.26100.0\win-x64\BiliDownloader.exe'
```

下载与封装需要 FFmpeg，开发时可在设置中选择路径。完整构建、发布与依赖准备方法见 [WinUI 3 开发与验证](docs/WINUI.md)。

```powershell
# 准备测试用 FFmpeg 后运行完整原生检查
.\scripts\verify_native.ps1
# 准备精简 FFmpeg 和 Inno Setup 6 后生成安装包
.\scripts\build_native_installer.ps1
```

3.0 已在开发机通过 23 项核心测试、26 组原生 UI 检查，以及生产发布版和隔离安装包检查。测试使用模拟 HTTP 与真实本地媒体，实网扫码、会员权限、其他 DPI 和干净系统安装仍需验收。具体场景与测量范围见[原生客户端审查报告](docs/WINUI-AUDIT-2026-11-06.md)。

- [WinUI 3 架构、运行、构建与验证](docs/WINUI.md)
- [更新日志](CHANGELOG.md)
- [v2.2.0 旧版使用与开发说明](docs/LEGACY-2.2.md)
- [第三方组件说明](THIRD_PARTY_NOTICES.md)

## 许可证

项目源码采用 [MIT](LICENSE)。发行包中的 FFmpeg 和其他第三方组件保留上游许可证，详见[第三方组件说明](THIRD_PARTY_NOTICES.md)和 Release 附件。
