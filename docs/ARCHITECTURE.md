# 源码架构

客户端使用 C#、.NET 10 和 WinUI 3，入口解决方案为 `BiliDownloader.sln`。

## 项目与资源

| 位置 | 职责 |
| --- | --- |
| `src/BiliDownloader.Core` | 解析、登录、网络、下载调度、限速、文件封装与持久化 |
| `src/BiliDownloader.WinUI` | 原生窗口、XAML 页面、列表与用户交互 |
| `src/BiliDownloader.WinUI/Assets` | 窗口头像等应用内容资源 |
| `tests/native` | 独立运行的 C# 核心回归程序 |
| `scripts` | PowerShell 构建与验证工具、测试构建专用 C# UI 场景 |
| `installer` | 原生安装脚本、语言文件与组件许可 |
| `artwork` | 用户提供的角色原图与资源说明 |

## 运行流程

`App` 创建原生窗口与单实例入口。`MainWindow` 初始化 `AppRuntime`，在同一窗口切换添加下载、队列、运行记录和设置页面。`AppRuntime` 汇集配置、登录服务、网络层和下载管理器。

解析通过 `BilibiliApi` 取得标题、分集与可用品质。用户选择内容、品质和保存目录后生成 `TaskSpec`，交给 `DownloadManager` 加入队列。管理器控制并发与限速，异步下载媒体流，通过独立 FFmpeg 进程封装，最后避让已有文件并发布结果。

UI 定时读取进度并复用任务行，切页保留解析结果和后台队列。配置、任务和登录信息由 `Storage` 持久化，任务记录写入使用临时文件与原子替换。

## 边界与状态

请求层负责 HTTPS、重定向限制与账号 Cookie 隔离。账号信息由当前 Windows 用户 DPAPI 保护，日志隐藏常见令牌。历史数据迁移在写入前建立备份。

下载状态依次覆盖等待、准备、下载、封装和完成，并支持失败与取消。失败或取消后可重试，退出时未完成的记录恢复为可重试状态。最终文件发布使用避让同名文件的移动操作。

## 构建与验证

生产构建包含核心项目与 WinUI 项目。启用 `UiRegression` 时额外编译 `scripts/WinUIRegression.cs` 和 `scripts/WinUIExperienceAudit.cs`，通过原生控件与 AutomationPeer 驱动测试。

GitHub Actions 检查原生项目与测试代码编译、发布资源、NuGet 依赖和 PowerShell 语法。本地完整脚本进一步执行真实媒体回归、UI 场景、发布启动与安装验证。运行方法见 [WinUI 开发说明](WINUI.md)和[构建说明](BUILDING.md)。
