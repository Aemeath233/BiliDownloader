using System.Diagnostics;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Runtime.InteropServices;
using System.Text.Json;
using BiliDownloader.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Storage;
using FileAttributes = System.IO.FileAttributes;

namespace BiliDownloader.WinUI;

internal sealed class WinUIExperienceAudit
{
    private readonly MainWindow window;
    private readonly Grid root;
    private readonly NavigationView navigation;
    private readonly string directory;
    private readonly AuditHandler fixture;
    private readonly object traceGate = new();
    private readonly List<object> checks = [];
    private readonly List<object> timings = [];
    private int failures;
    private AppRuntime runtime = null!;

    private WinUIExperienceAudit(MainWindow window, string directory)
    {
        this.window = window; this.directory = directory;
        root = Field<Grid>("Root"); navigation = Field<NavigationView>("Navigation");
        fixture = new AuditHandler(message => Trace("http", message));
    }
    public static async Task<object> RunAsync(MainWindow window, string directory)
    {
        var audit = new WinUIExperienceAudit(window, directory);
        return await audit.RunAsync();
    }
    private async Task<object> RunAsync()
    {
        await Field<AppRuntime>("runtime").DisposeAsync();
        var storage = new Storage(Path.Combine(directory, "experience-profile"));
        storage.SaveConfig(new AppConfig { SavePath = Path.Combine(storage.DirectoryPath, "downloads"), MaxConcurrentTasks = 4 });
        storage.SaveTasks(Enumerable.Range(1, 1000).Select(index => new DownloadRecord
        {
            TaskId = $"history-{index}", Title = $"课程 {index:D4} - Java 集合与并发专题", SourceUrl = "https://www.bilibili.com/video/BV17F411T7Ao",
            Episode = new("演示课程", "BV17F411T7Ao", index, false), Quality = new(80, "1080P"), SaveDir = storage.DirectoryPath,
            OutputPath = Path.Combine(storage.DirectoryPath, $"课程 {index:D4}.mp4"), CreatedAt = index, FinishedAt = index + 1,
            Status = (index % 3) switch { 0 => DownloadStatus.Failed, 1 => DownloadStatus.Completed, _ => DownloadStatus.Canceled },
            Message = index % 3 == 0 ? "演示网络错误，可重试" : "下载完成", Progress = new() { Percent = 100, TotalBytes = 1048576 }
        }));
        runtime = new AppRuntime(storage.DirectoryPath, new Network(fixture));
        typeof(MainWindow).GetField("runtime", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, runtime);
        Call("LoadSettings");
        Call("ApplyTheme");
        await ResizeAsync(1080, 720);
        await CheckAsync("队列空筛选与千条历史首次展示", async () =>
        {
            Navigate("queue");
            Field<ComboBox>("QueueFilter").SelectedIndex = 0;
            Field<TextBox>("QueueSearch").Text = "";
            await YieldAsync();
            Assert(Field<ListView>("QueueList").Items.Count == 1000, "History was not fully restored");
            Assert(Field<ListView>("QueueList").ContainerFromIndex(999) is null, "Queue is not virtualized");
        });
        await CheckAsync("页面切换 80 次与调度响应", async () =>
        {
            var ids = runtime.Downloads.Add(Enumerable.Range(9000, 4).Select(index => new TaskSpec("https://www.bilibili.com/video/BV17F411T7Ao",
                new Episode($"压力下载 {index}", "BV17F411T7Ao", index, false), new Quality(80, "1080P"), runtime.Config.SavePath, $"stress-{index}", DownloadMode.Video)));
            await WaitAsync(() => runtime.Downloads.Snapshot().Count(record => record.Status == DownloadStatus.Downloading) == 4);
            using var heartbeat = new Heartbeat(root.DispatcherQueue);
            foreach (var page in Enumerable.Range(0, 80).Select(index => new[] { "parse", "queue", "settings", "logs" }[index % 4]))
            {
                var watch = Stopwatch.StartNew();
                Navigate(page);
                await YieldAsync();
                timings.Add(new { action = "navigation", page, elapsed_ms = watch.Elapsed.TotalMilliseconds });
            }
            timings.Add(new { action = "dispatcher_heartbeat_during_navigation", heartbeat = heartbeat.Summary() });
            foreach (var id in ids) runtime.Downloads.Cancel(id);
            await WaitAsync(() => runtime.Downloads.Snapshot().All(record => record.IsTerminal));
        });
        await CheckAsync("大队列搜索与状态筛选", async () =>
        {
            Navigate("queue");
            var search = Field<TextBox>("QueueSearch");
            foreach (var text in new[] { "0", "00", "004", "0042", "not-found", "" })
            {
                var watch = Stopwatch.StartNew();
                search.Text = text;
                await YieldAsync();
                timings.Add(new { action = "queue_search", text, elapsed_ms = watch.Elapsed.TotalMilliseconds });
                if (text == "not-found") Assert(Field<StackPanel>("QueueEmpty").Visibility == Visibility.Visible, "No-results feedback missing");
            }
            Field<ComboBox>("QueueFilter").SelectedIndex = 2;
            await YieldAsync();
            Assert(Field<ListView>("QueueList").Items.Cast<TaskRow>().All(row => row.Record.Status == DownloadStatus.Completed), "Completed filter leaked another state");
            Field<ComboBox>("QueueFilter").SelectedIndex = 0;
            await YieldAsync();
        });
        await CheckAsync("跨筛选的批量选择与取消选择", async () =>
        {
            var queue = Field<ListView>("QueueList");
            queue.SelectedItems.Add(queue.Items[0]);
            var selected = ((TaskRow)queue.Items[0]).Id;
            Field<TextBox>("QueueSearch").Text = "not-found";
            await YieldAsync();
            Assert(Field<HashSet<string>>("selectedTasks").Contains(selected), "Filtering dropped selected task");
            Field<TextBox>("QueueSearch").Text = "";
            await YieldAsync();
            Assert(queue.SelectedItems.Cast<TaskRow>().Any(row => row.Id == selected), "Selection was not restored");
            Call("DeselectQueue", root, new RoutedEventArgs());
            Assert(!Field<Button>("BatchButton").IsEnabled, "Batch actions stayed enabled without selection");
        });
        await CheckAsync("慢解析时继续切页与旧请求取消", async () =>
        {
            Navigate("parse");
            Field<TextBox>("LinkInput").Text = "BV1111111111";
            await Task.Delay(520);
            Navigate("queue");
            Assert(Field<Grid>("QueuePage").Visibility == Visibility.Visible, "Parsing blocked navigation");
            Navigate("parse");
            Field<TextBox>("LinkInput").Text = "BV17F411T7Ao";
            await ParseReadyAsync();
            Assert(Field<MediaResult>("media").Title.Contains("Java"), "A stale parsing response replaced the new video");
        });
        await CheckAsync("200 集全选、反选及虚拟列表", async () =>
        {
            var episodes = Field<ListView>("EpisodeList");
            Assert(episodes.Items.Count == 200, "Expected 200-episode fixture");
            var watch = Stopwatch.StartNew();
            Call("SelectAllEpisodes", episodes, new RoutedEventArgs());
            await YieldAsync();
            timings.Add(new { action = "select_200_episodes", elapsed_ms = watch.Elapsed.TotalMilliseconds });
            Assert(episodes.SelectedItems.Count == 200, "Select-all missed episodes");
            Call("InvertEpisodes", episodes, new RoutedEventArgs());
            Assert(episodes.SelectedItems.Count == 0 && !Field<Button>("AddButton").IsEnabled, "Empty selection can be submitted");
            episodes.SelectedItems.Add(episodes.Items[0]);
            await ParseReadyAsync();
            Assert(episodes.ContainerFromIndex(199) is null, "Episode list is not virtualized");
        });
        await CheckAsync("三种下载模式的品质与文件后缀", async () =>
        {
            for (var index = 0; index < 3; index++)
            {
                Field<ComboBox>("ModeSelect").SelectedIndex = index;
                await ParseReadyAsync();
                var quality = (Quality)Field<ComboBox>("QualitySelect").SelectedItem;
                Assert((quality.Qn >= 30000) == (index == 1), "Audio and video quality options were mixed");
                Assert(Field<TextBlock>("FilenamePreview").Text.EndsWith(index == 1 ? ".m4a" : ".mp4"), "Filename extension does not match mode");
            }
            Field<ComboBox>("ModeSelect").SelectedIndex = 0;
            await ParseReadyAsync();
        });
        await CheckAsync("单视频隐藏分集列表并可直接下载", async () =>
        {
            Field<TextBox>("LinkInput").Text = "BV3333333333";
            await ParseReadyAsync();
            Assert(Field<MediaResult>("media").Episodes.Count == 1 && Field<Grid>("EpisodePanel").Visibility == Visibility.Collapsed && Field<Button>("AddButton").IsEnabled,
                "Single-video flow still requires a chapter selection");
            Assert(Grid.GetColumn(Field<StackPanel>("QualityOptions")) == 1, "Wide single-video options did not align side by side");
            await ScreenshotAsync("audit-single-video.png");
            await ResizeAsync(760, 560);
            Assert(Grid.GetRow(Field<StackPanel>("QualityOptions")) == 1, "Narrow single-video options did not stack");
            Reachable(Field<Button>("AddButton"));
            await ScreenshotAsync("audit-single-video-narrow.png");
            await ResizeAsync(1080, 720);
            Field<TextBox>("LinkInput").Text = "BV17F411T7Ao";
            await ParseReadyAsync();
        });
        await CheckAsync("长队列与分集列表滚动和容器回收", async () =>
        {
            foreach (var target in new[] { (Page: "parse", List: "EpisodeList"), (Page: "queue", List: "QueueList") })
            {
                Navigate(target.Page);
                await YieldAsync();
                var list = Field<ListView>(target.List);
                var scroll = Descendants(list).OfType<ScrollViewer>().First();
                foreach (var fraction in new[] { 0.25, 0.5, 0.75, 1.0, 0.5, 0.0 })
                {
                    var completed = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                    void Changed(object? sender, ScrollViewerViewChangedEventArgs args) { if (!args.IsIntermediate) completed.TrySetResult(); }
                    scroll.ViewChanged += Changed;
                    var watch = Stopwatch.StartNew();
                    try
                    {
                        if (scroll.ChangeView(null, scroll.ScrollableHeight * fraction, null, true))
                            await completed.Task.WaitAsync(TimeSpan.FromSeconds(2));
                        await YieldAsync();
                        timings.Add(new { action = "scroll_layout", list = target.List, fraction, elapsed_ms = watch.Elapsed.TotalMilliseconds,
                            realized_rows = Descendants(list).OfType<ListViewItem>().Count() });
                    }
                    finally { scroll.ViewChanged -= Changed; }
                    Assert(Descendants(list).OfType<ListViewItem>().Count() < 80, "Scrolling realized too many offscreen rows");
                }
            }
        });
        await CheckAsync("窄窗与浅深色主题的按钮可达性", async () =>
        {
            foreach (var size in new[] { (760, 560), (980, 640), (1080, 720) })
                foreach (var theme in new[] { ElementTheme.Light, ElementTheme.Dark })
                {
                    await ResizeAsync(size.Item1, size.Item2);
                    root.RequestedTheme = theme;
                    await Task.Delay(80);
                    foreach (var page in new[] { "parse", "queue", "settings", "logs" })
                    {
                        Navigate(page);
                        await YieldAsync();
                        foreach (var item in navigation.MenuItems.Cast<NavigationViewItem>()) Reachable(item);
                        if (page == "parse")
                        {
                            Reachable(Field<Button>("AddButton"));
                            Reachable(Field<Button>("DestinationButton"));
                            Reachable(Field<TextBox>("LinkInput"));
                            Reachable(Field<ComboBox>("ModeSelect"));
                            Reachable(Field<ComboBox>("QualitySelect"));
                        }
                        if (page == "queue")
                        {
                            foreach (var button in Descendants(Field<Grid>("QueuePage")).OfType<Button>().Where(button => button.Content is string text && text is "选择全部" or "重试失败 / 取消" or "清理已结束")) Reachable(button);
                            foreach (var row in Field<ListView>("QueueList").Items.Cast<TaskRow>().DistinctBy(row => row.Record.Status))
                                Assert(WinUIRegression.Contrast(row.StatusBrush.Color, ((SolidColorBrush)root.Background).Color) >= 4.5, "Small status text has insufficient contrast");
                        }
                        await ScreenshotAsync($"audit-{page}-{size.Item1}x{size.Item2}-{theme}.png");
                    }
                }
            root.RequestedTheme = ElementTheme.Light;
        });
        await CheckAsync("键盘焦点与可访问按钮语义", async () =>
        {
            Navigate("parse");
            Assert(Field<TextBox>("LinkInput").Focus(FocusState.Keyboard), "Link input cannot receive keyboard focus");
            var visited = new HashSet<string>();
            for (var i = 0; i < 18; i++)
            {
                FocusManager.TryMoveFocus(FocusNavigationDirection.Next, new FindNextElementOptions { SearchRoot = root });
                if (FocusManager.GetFocusedElement(root.XamlRoot) is FrameworkElement element) visited.Add(element.Name.Length > 0 ? element.Name : element.GetType().Name);
            }
            Assert(visited.Contains("AddButton") && visited.Contains("ModeSelect"), "Primary controls are missing from keyboard traversal");
            Assert(root.KeyboardAccelerators.Any(accelerator => accelerator.Key == Windows.System.VirtualKey.L && accelerator.Modifiers == Windows.System.VirtualKeyModifiers.Control), "Ctrl+L shortcut missing");
            var peer = FrameworkElementAutomationPeer.CreatePeerForElement(Field<Button>("AddButton"));
            Assert(peer is not null && peer.GetPattern(PatternInterface.Invoke) is IInvokeProvider && peer.GetName().Contains("下载"), "Download button cannot be discovered or invoked by accessibility tools");
        });
        await CheckAsync("后台下载中追加、重复提醒与取消", async () =>
        {
            Navigate("parse");
            Field<ComboBox>("QualitySelect").SelectedIndex = 1;
            await ParseReadyAsync();
            var count = runtime.Downloads.Snapshot().Count;
            Invoke(Field<Button>("AddButton"));
            await WaitAsync(() => runtime.Downloads.Snapshot().Count == count + 1 && !Field<bool>("adding"));
            await WaitAsync(() => runtime.Downloads.Snapshot().Any(record => record.Status == DownloadStatus.Downloading));
            Navigate("parse");
            Invoke(Field<Button>("AddButton"));
            await WaitAsync(() => OpenDialog() is not null);
            OpenDialog()!.Hide();
            await WaitAsync(() => !Field<bool>("adding"));
            Assert(runtime.Downloads.Snapshot().Count == count + 1, "Canceling duplicate warning still added a task");
            var active = runtime.Downloads.Snapshot().Single(record => !record.IsTerminal);
            runtime.Downloads.Cancel(active.TaskId);
            await WaitAsync(() => runtime.Downloads.Snapshot().Single(record => record.TaskId == active.TaskId).Status == DownloadStatus.Canceled);
        });
        await CheckAsync("任务状态变化时按需加载正确操作按钮", async () =>
        {
            Navigate("queue");
            var queue = Field<ListView>("QueueList");
            var row = (TaskRow)queue.Items[0];
            queue.ScrollIntoView(row);
            await YieldAsync();
            var original = row.Record;
            var timer = Field<Microsoft.UI.Dispatching.DispatcherQueueTimer>("refreshTimer");
            timer.Stop();
            try
            {
                foreach (var status in new[] { DownloadStatus.Downloading, DownloadStatus.Failed, DownloadStatus.Completed, DownloadStatus.Queued })
                {
                    row.Update(original with { Status = status }, root.ActualTheme);
                    await YieldAsync();
                    var container = (ListViewItem)queue.ContainerFromItem(row);
                    var buttons = Descendants(container).OfType<Button>().Where(button => button.Name.EndsWith("TaskButton")).Select(button => button.Name).ToHashSet();
                    var expected = status switch
                    {
                        DownloadStatus.Downloading => new[] { "CancelTaskButton" },
                        DownloadStatus.Failed => new[] { "RetryTaskButton", "MoreTaskButton", "RemoveTaskButton" },
                        DownloadStatus.Completed => new[] { "OpenTaskButton", "LocateTaskButton", "RemoveTaskButton" },
                        _ => new[] { "CancelTaskButton", "RemoveTaskButton" }
                    };
                    Assert(buttons.SetEquals(expected), $"Wrong lazy-loaded buttons for {status}: {string.Join(",", buttons)}");
                }
            }
            finally { row.Update(original, root.ActualTheme); timer.Start(); }
        });
        await CheckAsync("失败记录重新选品质保留模式与目录", async () =>
        {
            var task = runtime.Downloads.Snapshot().First(record => record.CanRetry) with { };
            var storage = runtime.Storage;
            var imported = task with { TaskId = "reselect-audio", DownloadMode = DownloadMode.Audio, SaveDir = Path.Combine(storage.DirectoryPath, "chosen-folder"), Quality = new(30280, "源音频") };
            // Use a normal retryable task with audio mode, then exercise its UI reselect command.
            runtime.Downloads.Add([new(imported.SourceUrl, imported.Episode, imported.Quality, imported.SaveDir, "reselect", imported.DownloadMode)]);
            var id = runtime.Downloads.Snapshot().Last().TaskId;
            runtime.Downloads.Cancel(id);
            await WaitAsync(() => runtime.Downloads.Snapshot().Single(record => record.TaskId == id).CanRetry);
            var sender = new MenuFlyoutItem { Tag = id };
            Call("ReselectTask", sender, new RoutedEventArgs());
            await ParseReadyAsync();
            await Task.Delay(650);
            Assert(Field<ComboBox>("ModeSelect").SelectedIndex == 1 && Field<TextBlock>("Destination").Text == imported.SaveDir,
                "Reselect lost audio mode or per-task directory");
        });
        await CheckAsync("已下架分集重选时拒绝替换为其他分集", async () =>
        {
            var spec = new TaskSpec("https://www.bilibili.com/video/BV17F411T7Ao", new("已下架分集", "BV17F411T7Ao", 999999, false), new(80, "1080P"), runtime.Config.SavePath, "missing-episode", DownloadMode.Video);
            var id = runtime.Downloads.Add([spec])[0];
            runtime.Downloads.Cancel(id);
            await WaitAsync(() => runtime.Downloads.Snapshot().Single(record => record.TaskId == id).CanRetry);
            Call("ReselectTask", new MenuFlyoutItem { Tag = id }, new RoutedEventArgs());
            await WaitAsync(() => Field<CancellationTokenSource?>("parseCancellation") is null && !Field<Button>("AddButton").IsEnabled && Field<InfoBar>("Notice").Message?.Contains("分集") == true);
            Assert(Field<MediaResult?>("media") is null, "Unavailable episode silently selected another episode");
        });
        await CheckAsync("日志刷新保留列表与滚动位置", async () =>
        {
            for (var i = 0; i < 100; i++) runtime.Log($"运行过程 {i}");
            Navigate("logs");
            var logs = Field<ListView>("LogList");
            var scroll = Descendants(logs).OfType<ScrollViewer>().First();
            scroll.ChangeView(null, 300, null, true);
            await Task.Delay(100);
            var source = logs.ItemsSource;
            var offset = scroll.VerticalOffset;
            await Task.Delay(1100);
            Assert(ReferenceEquals(source, logs.ItemsSource) && Math.Abs(offset - scroll.VerticalOffset) < 1, "Idle log refresh replaced the list or moved scrolling");
        });
        await CheckAsync("无动画设置与解析错误提示", async () =>
        {
            Field<ToggleSwitch>("SettingAnimations").IsOn = false;
            Call("SaveSettings", root, new RoutedEventArgs());
            Assert(navigation.ContentTransitions.Count == 0, "Reduced-animation setting was ignored");
            Navigate("parse");
            Field<TextBox>("LinkInput").Text = "https://evil.invalid/video";
            Invoke(Field<Button>("ParseButton"));
            await WaitAsync(() => Field<InfoBar>("Notice").IsOpen);
            Assert(!Field<Button>("AddButton").IsEnabled && Field<InfoBar>("Notice").Severity == InfoBarSeverity.Error, "Invalid link has no clear error state");
        });
        await CheckAsync("登录文件不可删除时保留账号并显示错误", async () =>
        {
            runtime.Auth.SetSessdata("isolated-test-cookie");
            File.SetAttributes(runtime.Storage.AuthPath, FileAttributes.ReadOnly);
            try
            {
                Call("AccountClicked", Field<Button>("AccountButton"), new RoutedEventArgs());
                await WaitAsync(() => OpenDialog() is not null);
                var button = Descendants(OpenDialog()!).OfType<Button>().Single(element => element.Name == "PrimaryButton");
                var peer = FrameworkElementAutomationPeer.CreatePeerForElement(button);
                ((IInvokeProvider)peer!.GetPattern(PatternInterface.Invoke)).Invoke();
                await WaitAsync(() => !Field<bool>("dialogOpen"));
                Assert(runtime.Auth.IsLoggedIn && Field<InfoBar>("Notice").IsOpen && Field<InfoBar>("Notice").Severity == InfoBarSeverity.Error,
                    "Failed logout lost the session or crashed without an error");
            }
            finally { File.SetAttributes(runtime.Storage.AuthPath, FileAttributes.Normal); runtime.Auth.SetSessdata(""); }
        });
        await CheckAsync("退出时的下载保护提示", async () =>
        {
            var task = new TaskSpec("https://www.bilibili.com/video/BV17F411T7Ao", new("关闭测试", "BV17F411T7Ao", 999, false), new(80, "1080P"), runtime.Config.SavePath, "close-test", DownloadMode.Video);
            runtime.Downloads.Add([task]);
            await WaitAsync(() => runtime.Downloads.Snapshot().Any(record => !record.IsTerminal));
            PostMessage(WinRT.Interop.WindowNative.GetWindowHandle(window), 0x0010, 0, 0);
            await WaitAsync(() => OpenDialog() is not null);
            Assert(OpenDialog()!.PrimaryButtonText.Contains("最小化") && OpenDialog()!.SecondaryButtonText.Contains("退出"), "Exit prompt does not explain the choices");
            OpenDialog()!.Hide();
            await WaitAsync(() => !Field<bool>("dialogOpen"));
            await Task.Delay(250);
            Assert(!Field<bool>("closing"), "Canceling exit still shut down the app");
        });
        var report = new { stage = "experience_audit", passed = failures == 0, failures, checks, timings,
            process = new { working_set_bytes = Process.GetCurrentProcess().WorkingSet64, private_bytes = Process.GetCurrentProcess().PrivateMemorySize64 },
            scope = "In-process WinUI controls and automation peers, synthetic HTTP and local profile. Dispatcher timings do not measure GPU frame presentation or human comprehension." };
        File.WriteAllText(Path.Combine(directory, "experience-report.json"), JsonSerializer.Serialize(report, Storage.Json));
        return report;
    }
    private async Task CheckAsync(string name, Func<Task> test)
    {
        var watch = Stopwatch.StartNew();
        Trace("start", name);
        try { await test(); checks.Add(new { name, passed = true, elapsed_ms = watch.Elapsed.TotalMilliseconds }); Trace("pass", name); }
        catch (Exception ex)
        {
            failures++;
            checks.Add(new { name, passed = false, error = ex.ToString(), elapsed_ms = watch.Elapsed.TotalMilliseconds });
            Trace("fail", name + " " + ex.Message);
            OpenDialog()?.Hide();
            await YieldAsync();
            await Task.Delay(100);
            if (root.XamlRoot is not null) await ScreenshotAsync($"failure-{failures}.png");
        }
        File.WriteAllText(Path.Combine(directory, "experience-progress.json"), JsonSerializer.Serialize(new { failures, checks, timings }, Storage.Json));
    }
    private void Trace(string state, string message)
    {
        lock (traceGate) File.AppendAllText(Path.Combine(directory, "experience-trace.jsonl"), JsonSerializer.Serialize(new { time = DateTimeOffset.UtcNow, state, message }) + Environment.NewLine);
    }
    private T Field<T>(string name) => (T)typeof(MainWindow).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window)!;
    private void Call(string name, params object?[] args) => typeof(MainWindow).GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, args);
    private void Navigate(string tag) { Trace("navigate", tag); navigation.SelectedItem = navigation.MenuItems.Cast<NavigationViewItem>().First(item => Equals(item.Tag, tag)); root.UpdateLayout(); }
    private async Task ParseReadyAsync()
    {
        await YieldAsync();
        await WaitAsync(() => Field<MediaResult?>("media") is not null && !Field<Microsoft.UI.Dispatching.DispatcherQueueTimer>("pasteTimer").IsRunning &&
            Field<CancellationTokenSource?>("parseCancellation") is null && Field<CancellationTokenSource?>("qualityCancellation") is null && Field<ComboBox>("QualitySelect").SelectedItem is Quality);
    }
    private void Invoke(Button button)
    {
        Trace("invoke", button.Name);
        var peer = FrameworkElementAutomationPeer.CreatePeerForElement(button);
        if (peer?.GetPattern(PatternInterface.Invoke) is not IInvokeProvider invoke) throw new Exception("Button has no invoke pattern");
        invoke.Invoke();
    }
    private ContentDialog? OpenDialog() => root.XamlRoot is null ? null : VisualTreeHelper.GetOpenPopupsForXamlRoot(root.XamlRoot).SelectMany(popup => Descendants(popup.Child)).OfType<ContentDialog>().FirstOrDefault();
    [DllImport("user32.dll")] private static extern bool PostMessage(nint window, uint message, nint wParam, nint lParam);
    private void Reachable(FrameworkElement element)
    {
        var point = element.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point(element.ActualWidth / 2, element.ActualHeight / 2));
        Assert(element.ActualWidth >= 24 && element.ActualHeight >= 24 && point.X >= 0 && point.X < root.ActualWidth && point.Y >= 0 && point.Y < root.ActualHeight &&
            VisualTreeHelper.FindElementsInHostCoordinates(point, root).Contains(element), $"Control is clipped or covered at {root.ActualWidth}x{root.ActualHeight}: {element.Name} {element.GetType().Name}");
    }
    private async Task ResizeAsync(int width, int height)
    {
        var scale = root.XamlRoot.RasterizationScale;
        var chromeWidth = window.AppWindow.Size.Width - root.ActualWidth * scale;
        var chromeHeight = window.AppWindow.Size.Height - root.ActualHeight * scale;
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32((int)(width * scale + chromeWidth), (int)(height * scale + chromeHeight)));
        await Task.Delay(100);
        root.UpdateLayout();
    }
    private Task YieldAsync()
    {
        var source = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        root.DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () => { root.UpdateLayout(); source.SetResult(); });
        return source.Task;
    }
    private async Task ScreenshotAsync(string name)
    {
        await YieldAsync();
        await Task.Delay(300);
        var image = new RenderTargetBitmap(); await image.RenderAsync(root);
        var pixels = await image.GetPixelsAsync();
        var folder = await StorageFolder.GetFolderFromPathAsync(directory);
        var file = await folder.CreateFileAsync(name, CreationCollisionOption.ReplaceExisting);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied, (uint)image.PixelWidth, (uint)image.PixelHeight, 96, 96, pixels.ToArray());
        await encoder.FlushAsync();
    }
    private static async Task WaitAsync(Func<bool> condition)
    {
        for (var i = 0; i < 160; i++) { if (condition()) return; await Task.Delay(50); }
        throw new TimeoutException("Expected UI state did not arrive within eight seconds");
    }
    private static void Assert(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static IEnumerable<DependencyObject> Descendants(DependencyObject element)
    {
        yield return element;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(element); i++)
            foreach (var child in Descendants(VisualTreeHelper.GetChild(element, i))) yield return child;
    }
    private sealed class Heartbeat : IDisposable
    {
        private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer timer;
        private readonly Stopwatch watch = Stopwatch.StartNew();
        private readonly List<double> gaps = [];
        private double previous;
        public Heartbeat(Microsoft.UI.Dispatching.DispatcherQueue dispatcher)
        {
            timer = dispatcher.CreateTimer(); timer.Interval = TimeSpan.FromMilliseconds(16);
            timer.Tick += (_, _) => { var now = watch.Elapsed.TotalMilliseconds; gaps.Add(now - previous); previous = now; };
            timer.Start();
        }
        public object Summary()
        {
            timer.Stop();
            var sorted = gaps.Order().ToList();
            return new { samples = sorted.Count, p95_ms = sorted.Count > 0 ? sorted[(int)((sorted.Count - 1) * 0.95)] : 0, max_ms = sorted.DefaultIfEmpty().Max(), gaps_over_50ms = sorted.Count(value => value > 50) };
        }
        public void Dispose() => timer.Stop();
    }
    private sealed class AuditHandler(Action<string> trace) : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            trace($"{request.Method} {request.RequestUri!.Host}{request.RequestUri.AbsolutePath}");
            if (request.RequestUri!.Host == "fixture.invalid") return new(HttpStatusCode.OK) { RequestMessage = request, Content = new BlockingContent() };
            await Task.Delay(request.RequestUri.Query.Contains("BV1111111111") ? 700 : 90, ct);
            var video = new { id = 80, baseUrl = "https://fixture.invalid/video", codecid = 7 };
            object data = request.RequestUri.AbsolutePath.Contains("playurl")
                ? new { code = 0, data = (object)new { accept_quality = new[] { 80, 64 }, accept_description = new[] { "1080P", "720P" }, dash = new
                { video = new[] { video, new { id = 64, baseUrl = "https://fixture.invalid/video-720", codecid = 7 } }, audio = new[] { new { id = 30280, baseUrl = "https://fixture.invalid/audio", bandwidth = 192000 } } } } }
                : new { code = 0, data = (object)new { title = "Java 系统课程 · 集合与并发专题", owner = new { name = "课程演示" },
                    pages = Enumerable.Range(1, request.RequestUri.Query.Contains("BV3333333333") ? 1 : 200).Select(index => new { page = index, cid = index, part = $"第 {index} 课 HashMap 源码详解与练习" }).ToArray() } };
            return new(HttpStatusCode.OK) { RequestMessage = request, Content = new StringContent(JsonSerializer.Serialize(data)) };
        }
    }
    private sealed class BlockingContent : HttpContent
    {
        protected override bool TryComputeLength(out long length) { length = 1048576; return true; }
        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) => throw new NotSupportedException();
        protected override Task<Stream> CreateContentReadStreamAsync() => Task.FromResult<Stream>(new BlockingStream());
    }
    private sealed class BlockingStream : Stream
    {
        public override bool CanRead => true; public override bool CanSeek => false; public override bool CanWrite => false;
        public override long Length => 1048576;
        public override long Position { get => 0; set => throw new NotSupportedException(); }
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken ct = default) { await Task.Delay(Timeout.Infinite, ct); return 0; }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override void Flush() { }
    }
}
