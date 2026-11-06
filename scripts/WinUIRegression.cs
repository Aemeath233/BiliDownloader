using System.Reflection;
using System.Net;
using System.Text.Json;
using BiliDownloader.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Storage;
using System.Runtime.InteropServices.WindowsRuntime;

namespace BiliDownloader.WinUI;

internal static class WinUIRegression
{
    public static async Task RunAsync()
    {
        var arguments = Environment.GetCommandLineArgs();
        var output = arguments[Array.IndexOf(arguments, "--report") + 1];
        var results = new List<object>();
        MainWindow? window = null;
        var fixture = new FixtureHandler();
        try
        {
            window = new MainWindow(Path.Combine(Path.GetDirectoryName(output)!, "profile"));
            window.Activate();
            for (var i = 0; i < 100 && !Field<bool>(window, "initialized"); i++) await Task.Delay(100);
            if (!Field<bool>(window, "initialized")) throw new Exception("Window failed to initialize");
            var startupToReady = (DateTime.UtcNow - System.Diagnostics.Process.GetCurrentProcess().StartTime.ToUniversalTime()).TotalMilliseconds;
            var startupWorkingSet = System.Diagnostics.Process.GetCurrentProcess().WorkingSet64;
            var root = Field<Grid>(window, "Root");
            var navigation = Field<NavigationView>(window, "Navigation");
            var parse = Field<NavigationViewItem>(window, "ParseNav");
            var queue = Field<NavigationViewItem>(window, "QueueNav");
            var workArea = Microsoft.UI.Windowing.DisplayArea.GetFromWindowId(window.AppWindow.Id, Microsoft.UI.Windowing.DisplayAreaFallback.Primary).WorkArea;
            var scale = root.XamlRoot.RasterizationScale;
            if (window.AppWindow.Size.Width != (int)Math.Min(1080 * scale, workArea.Width * 0.9) ||
                window.AppWindow.Size.Height != (int)Math.Min(720 * scale, workArea.Height * 0.9))
                throw new Exception("Default window dimensions do not respect display scaling and work area");
            if (Field<TextBlock>(window, "PageTitle").FontSize != 22) throw new Exception("Compact heading size was not applied");
            await ScreenshotAsync(root, Path.Combine(Path.GetDirectoryName(output)!, "default-window.png"));
            results.Add(new { stage = "default_window_and_density", scale, width = root.ActualWidth, height = root.ActualHeight, passed = true,
                startup_to_ready_ms = startupToReady, working_set_bytes = startupWorkingSet });
            await Field<AppRuntime>(window, "runtime").DisposeAsync();
            var fixtureProfile = Path.Combine(Path.GetDirectoryName(output)!, "profiles", Guid.NewGuid().ToString("N"));
            var fixtureStorage = new Storage(fixtureProfile);
            fixtureStorage.Write("config.json", new { steady_mode = true, max_concurrent_tasks = 4, save_path = Path.Combine(fixtureProfile, "downloads") });
            var testRuntime = new AppRuntime(fixtureProfile, new Network(fixture));
            typeof(MainWindow).GetField("runtime", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, testRuntime);
            typeof(MainWindow).GetMethod("LoadSettings", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
            typeof(MainWindow).GetMethod("SaveSettings", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [Field<Grid>(window, "Root"), new RoutedEventArgs()]);
            if (testRuntime.Config.MaxConcurrentTasks != 4 || File.ReadAllText(Path.Combine(fixtureProfile, "config.json")).Contains("steady_mode"))
                throw new Exception("Old steady-mode setting was not retired");
            testRuntime.Downloads.Add([new("https://www.bilibili.com/video/BV17F411T7Ao", new("原有下载任务", "BV17F411T7Ao", 999, false), new(80, "1080P"),
                Path.Combine(testRuntime.Storage.DirectoryPath, "downloads"), "original", DownloadMode.Video)]);
            await WaitAsync(() => testRuntime.Downloads.Snapshot().Any(r => r.Status == DownloadStatus.Downloading), "Original download did not start");
            navigation.SelectedItem = queue;
            root.UpdateLayout();
            navigation.SelectedItem = parse;
            Field<TextBox>(window, "LinkInput").Text = "https://www.bilibili.com/video/BV17F411T7Ao";
            await WaitAsync(() => Field<MediaResult?>(window, "media") is not null && Field<ComboBox>(window, "QualitySelect").SelectedItem is Quality
                && Field<CancellationTokenSource?>(window, "parseCancellation") is null, "Automatic parsing did not finish");
            var list = Field<ListView>(window, "EpisodeList");
            var selected = (EpisodeRow)list.SelectedItems[0];
            if (!selected.DisplayTitle.StartsWith("P1 ") || selected.Title.Length <= 180) throw new Exception("Long source title was lost or repeated in chapter label");
            foreach (var width in new[] { 1600, 1180 })
            {
                window.AppWindow.Resize(new Windows.Graphics.SizeInt32(width, 900));
                await Task.Delay(300);
                root.UpdateLayout();
                foreach (var item in navigation.MenuItems.Cast<NavigationViewItem>())
                {
                    var point = item.TransformToVisual(root).TransformPoint(new Windows.Foundation.Point(item.ActualWidth / 2, item.ActualHeight / 2));
                    if (item.ActualWidth <= 0 || !VisualTreeHelper.FindElementsInHostCoordinates(point, root).Contains(item))
                        throw new Exception($"Navigation item is not reachable at {root.ActualWidth} DIPs: {item.Content}");
                }
                var text = Descendants(list).OfType<TextBlock>().First(t => t.Text == selected.DisplayTitle);
                if (text.TextWrapping != TextWrapping.NoWrap || text.MaxLines != 1 || !text.IsTextTrimmed || text.ActualHeight > 30)
                    throw new Exception("Episode text did not stay on one ellipsized line");
                if (!Equals(ToolTipService.GetToolTip(text), selected.Title)) throw new Exception("Full episode tooltip missing");
                results.Add(new { stage = "navigation_and_titles", width = root.ActualWidth, passed = true });
                await ScreenshotAsync(root, Path.Combine(Path.GetDirectoryName(output)!, $"episodes-{(int)root.ActualWidth}.png"));
            }
            await ScreenshotAsync(root, Path.Combine(Path.GetDirectoryName(output)!, "episodes.png"));
            await CheckSelectionAppearanceAsync(list, root, Path.GetDirectoryName(output)!, "episodes");
            results.Add(new { stage = "episode_checkbox_contrast_light_and_dark", passed = true });
            navigation.SelectedItem = queue;
            root.UpdateLayout();
            await Task.Delay(300);
            if (Field<Grid>(window, "QueuePage").Visibility != Visibility.Visible) throw new Exception("Queue navigation failed");
            navigation.SelectedItem = parse;
            root.UpdateLayout();
            if (Field<Grid>(window, "ParsePage").Visibility != Visibility.Visible) throw new Exception("Parse navigation failed");
            if (!ReferenceEquals(list.SelectedItems[0], selected)) throw new Exception("Episode selection was lost");
            typeof(MainWindow).GetMethod("AddClicked", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [Field<Button>(window, "AddButton"), new RoutedEventArgs()]);
            await WaitAsync(() => testRuntime.Downloads.Snapshot().Count == 2 && !Field<bool>(window, "adding"), "Appending a second task failed");
            if (!testRuntime.Downloads.Snapshot().All(r => !r.IsTerminal)) throw new Exception("Existing download was stopped when adding a task");
            navigation.SelectedItem = parse;
            if (Field<Grid>(window, "ParsePage").Visibility != Visibility.Visible || !Field<TextBox>(window, "LinkInput").IsEnabled)
                throw new Exception("Cannot parse again while downloading");
            navigation.SelectedItem = queue;
            root.UpdateLayout();
            await Task.Delay(300);
            var queueTitles = Descendants(Field<ListView>(window, "QueueList")).OfType<TextBlock>().Where(t => t.Text == selected.Title).ToList();
            if (queueTitles.Count != 1 || queueTitles[0].MaxLines != 1 || !Equals(ToolTipService.GetToolTip(queueTitles[0]), selected.Title))
                throw new Exception("Queue title tooltip or one-line layout missing");
            await ScreenshotAsync(root, Path.Combine(Path.GetDirectoryName(output)!, "queue.png"));
            await CheckSelectionAppearanceAsync(Field<ListView>(window, "QueueList"), root, Path.GetDirectoryName(output)!, "queue");
            results.Add(new { stage = "queue_checkbox_contrast_light_and_dark", passed = true });
            results.Add(new { stage = "parse_and_append_during_download", count = 2, passed = true });
            testRuntime.Downloads.Add(Enumerable.Range(900, 3).Select(cid => new TaskSpec("https://www.bilibili.com/video/BV17F411T7Ao",
                new Episode($"并发验证 {cid}", "BV17F411T7Ao", cid, false), new Quality(80, "1080P"), testRuntime.Config.SavePath, $"concurrency-{cid}", DownloadMode.Video)));
            await WaitAsync(() => testRuntime.Downloads.Snapshot().Count(r => r.Status == DownloadStatus.Downloading) == 4, "Configured concurrency was capped below four");
            await Task.Delay(400);
            var concurrent = testRuntime.Downloads.Snapshot();
            if (concurrent.Count(r => r.Status == DownloadStatus.Downloading) != 4 || concurrent.Count(r => r.Status == DownloadStatus.Queued) != 1)
                throw new Exception("Scheduler did not enforce the configured four-task limit");
            var metrics = testRuntime.Downloads.Metrics();
            typeof(MainWindow).GetMethod("Refresh", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
            if (metrics.Active != 4 || metrics.Queued != 1 || !Field<TextBlock>(window, "StatusLine").Text.Contains("1 个排队"))
                throw new Exception("Status bar confused active downloads with queued tasks");
            results.Add(new { stage = "legacy_config_and_four_concurrent_downloads", downloading = 4, queued = 1, passed = true });
            navigation.SelectedItem = navigation.MenuItems.Cast<NavigationViewItem>().First(item => Equals(item.Tag, "settings"));
            root.UpdateLayout();
            if (Descendants(Field<ScrollViewer>(window, "SettingsPage")).OfType<TextBlock>().Any(t => t.Text.Contains("稳妥模式")))
                throw new Exception("Retired steady-mode UI is still present");
            await ScreenshotAsync(root, Path.Combine(Path.GetDirectoryName(output)!, "settings.png"));
            if (arguments.Contains("--experience"))
            {
                var audit = await WinUIExperienceAudit.RunAsync(window, Path.GetDirectoryName(output)!);
                results.Add(audit);
                if (!JsonSerializer.SerializeToElement(audit).GetProperty("passed").GetBoolean()) throw new Exception("Experience audit failed. See experience-report.json");
            }
            File.WriteAllText(output, JsonSerializer.Serialize(new { passed = true, results }, Storage.Json));
        }
        catch (Exception ex) { File.WriteAllText(output, JsonSerializer.Serialize(new { passed = false, error = ex.ToString(), results }, Storage.Json)); }
        finally
        {
            if (window is not null)
            {
                Field<Microsoft.UI.Dispatching.DispatcherQueueTimer>(window, "refreshTimer").Stop();
                Field<Microsoft.UI.Dispatching.DispatcherQueueTimer>(window, "pasteTimer").Stop();
                typeof(MainWindow).GetField("closing", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, true);
                var runtime = Field<AppRuntime?>(window, "runtime");
                if (runtime is not null) await runtime.DisposeAsync();
                window.GetType().GetField("allowClose", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, true);
                window.Close();
            }
        }
    }
    private static T Field<T>(MainWindow window, string name) => (T)typeof(MainWindow).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(window)!;
    private static async Task CheckSelectionAppearanceAsync(ListView list, Grid root, string directory, string name)
    {
        var originalTheme = root.RequestedTheme;
        var selected = list.SelectedItems.ToArray();
        try
        {
            list.SelectedItems.Clear();
            list.SelectedItems.Add(list.Items[0]);
            foreach (var theme in new[] { ElementTheme.Light, ElementTheme.Dark })
            {
                root.RequestedTheme = theme;
                await Task.Delay(250);
                root.UpdateLayout();
                var presenters = Descendants(list).OfType<ListViewItemPresenter>().ToList();
                if (presenters.Count < 2) throw new Exception("Expected selected and unselected list items");
                var page = ((SolidColorBrush)root.Background).Color;
                foreach (var presenter in presenters)
                {
                    if (presenter.CheckBoxBorderBrush is not SolidColorBrush border || presenter.CheckBoxSelectedBrush is not SolidColorBrush fill ||
                        presenter.CheckBrush is not SolidColorBrush glyph || presenter.CheckBoxBrush is not SolidColorBrush uncheckedFill)
                        throw new Exception($"{name}/{theme} checkbox is missing its native selection brushes");
                    var background = Composite(uncheckedFill.Color, page);
                    if (Contrast(Composite(border.Color, background), background) < 3)
                        throw new Exception($"{name}/{theme} unchecked checkbox outline lacks contrast");
                    var checkedBackground = Composite(fill.Color, page);
                    if (Contrast(Composite(glyph.Color, checkedBackground), checkedBackground) < 3)
                        throw new Exception($"{name}/{theme} selected checkbox glyph lacks contrast");
                }
                await ScreenshotAsync(root, Path.Combine(directory, $"{name}-checkboxes-{theme.ToString().ToLowerInvariant()}.png"));
            }
        }
        finally
        {
            root.RequestedTheme = originalTheme;
            list.SelectedItems.Clear();
            foreach (var item in selected) list.SelectedItems.Add(item);
        }
    }
    private static Windows.UI.Color Composite(Windows.UI.Color foreground, Windows.UI.Color background)
    {
        var alpha = foreground.A / 255.0;
        return Windows.UI.Color.FromArgb(255, (byte)Math.Round(foreground.R * alpha + background.R * (1 - alpha)),
            (byte)Math.Round(foreground.G * alpha + background.G * (1 - alpha)), (byte)Math.Round(foreground.B * alpha + background.B * (1 - alpha)));
    }
    internal static double Contrast(Windows.UI.Color first, Windows.UI.Color second)
    {
        static double Linear(byte value) => value / 255.0 <= 0.04045 ? value / 255.0 / 12.92 : Math.Pow((value / 255.0 + 0.055) / 1.055, 2.4);
        static double Luminance(Windows.UI.Color color) => 0.2126 * Linear(color.R) + 0.7152 * Linear(color.G) + 0.0722 * Linear(color.B);
        var a = Luminance(first);
        var b = Luminance(second);
        return (Math.Max(a, b) + 0.05) / (Math.Min(a, b) + 0.05);
    }
    private static async Task ScreenshotAsync(FrameworkElement root, string path)
    {
        var bitmap = new RenderTargetBitmap();
        await bitmap.RenderAsync(root);
        var pixels = await bitmap.GetPixelsAsync();
        var folder = await StorageFolder.GetFolderFromPathAsync(Path.GetDirectoryName(path)!);
        var file = await folder.CreateFileAsync(Path.GetFileName(path), CreationCollisionOption.ReplaceExisting);
        using var stream = await file.OpenAsync(FileAccessMode.ReadWrite);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied, (uint)bitmap.PixelWidth, (uint)bitmap.PixelHeight, 96, 96, pixels.ToArray());
        await encoder.FlushAsync();
    }
    private static async Task WaitAsync(Func<bool> condition, string message)
    {
        for (var i = 0; i < 100; i++) { if (condition()) return; await Task.Delay(50); }
        throw new Exception(message);
    }
    private static IEnumerable<DependencyObject> Descendants(DependencyObject parent)
    {
        yield return parent;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
            foreach (var child in Descendants(VisualTreeHelper.GetChild(parent, i))) yield return child;
    }
    private sealed class FixtureHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var title = string.Concat(Enumerable.Repeat("Java零基础视频教程与练习题", 20));
            var chapter = string.Concat(Enumerable.Repeat("注释和关键字的使用方法", 15));
            var data = request.RequestUri!.AbsolutePath.Contains("playurl")
                ? new { code = 0, data = (object)new { accept_quality = new[] { 80 }, accept_description = new[] { "1080P" }, dash = new { video = new[] { new { id = 80, baseUrl = "https://fixture.invalid/video", codecid = 7 } } } } }
                : new { code = 0, data = (object)new { title, owner = new { name = "演示课程" }, pages = new[] { new { page = 1, part = chapter, cid = 1 }, new { page = 2, part = "打开 CMD", cid = 2 } } } };
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { RequestMessage = request,
                Content = request.RequestUri.Host == "fixture.invalid" ? new PendingContent() : new StringContent(JsonSerializer.Serialize(data)) });
        }
    }
    private sealed class PendingContent : HttpContent
    {
        protected override bool TryComputeLength(out long length) { length = 1048576; return true; }
        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) => throw new NotSupportedException();
        protected override Task<Stream> CreateContentReadStreamAsync() => Task.FromResult<Stream>(new PendingStream());
    }
    private sealed class PendingStream : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => 1048576;
        public override long Position { get => 0; set => throw new NotSupportedException(); }
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        { await Task.Delay(Timeout.Infinite, cancellationToken); return 0; }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override void Flush() { }
    }
}
