# 应用角色图标

`sky-blue-ribbon.png` 是用户于 2026-11-03 提供并选定的透明背景原图，保留用于后续导出。

当前应用使用根目录的 `logo.ico`，包含 16、24、32、48、64、128 和 256 像素帧。窗口标题栏使用 `src/BiliDownloader.WinUI/Assets/brand.png`。导出时保留原画，居中缩放并增加约 6% 的透明边距。

头像作为 WinUI 内容资源复制到程序目录，通过 `ms-appx:///Assets/brand.png` 加载。原生项目和 Inno Setup 共用 `logo.ico`，用于程序文件、快捷方式和安装程序。
