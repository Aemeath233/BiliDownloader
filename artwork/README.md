# 应用角色图标

`sky-blue-ribbon.png` 是用户于 2026-11-03 提供并选定的透明背景原图，保留用于后续导出。

当前应用使用根目录的 `logo.ico`，包含 16、24、32、48、64、128 和 256 像素帧；前端侧栏使用 `bili_client/desktop/assets/brand.png`。导出时保留原画，居中缩放并增加约 6% 的透明边距，没有重绘人物。

前端图片由 `asset_html()` 内嵌为 data URL，安装后无需网络或外部图片路径。PyInstaller 和 Inno Setup 共用 `logo.ico`，分别用于程序窗口、程序文件、快捷方式和安装程序。
