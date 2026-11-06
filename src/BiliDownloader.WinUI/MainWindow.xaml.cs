using System.Diagnostics;
using BiliDownloader.Core;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media.Animation;
using QRCoder;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;
using Windows.Storage.Streams;
using Windows.System;
using WinRT.Interop;

namespace BiliDownloader.WinUI;

public sealed partial class MainWindow : Window
{
    private AppRuntime? runtime;
    private readonly string? profile;
    private readonly TaskRowCollection taskRows = new();
    private readonly Dictionary<string, TaskRow> rows = new();
    private readonly HashSet<string> selectedTasks = [];
    private readonly Dictionary<DownloadMode, int> qualityMemory = new();
    private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer refreshTimer;
    private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer pasteTimer;
    private CancellationTokenSource? parseCancellation;
    private CancellationTokenSource? qualityCancellation;
    private CancellationTokenSource? qrCancellation;
    private MediaResult? media;
    private string? destinationOverride;
    private string? programmaticLink;
    private bool initialized, applyingMedia, syncingQueue, adding, closing, allowClose, dialogOpen;
    private bool resetQueueScroll;
    private string currentPage = "parse";
    private long qualityEpisodeCid;
    private DownloadMode Mode => (DownloadMode)Math.Max(0, ModeSelect.SelectedIndex);

    public MainWindow(string? profile = null)
    {
        this.profile = profile;
        InitializeComponent();
        Title = "BiliDownloader";
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TitleRegion);
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "logo.ico"));
        AppWindow.Closing += WindowClosing;
        Root.Loaded += Loaded;
        Root.ActualThemeChanged += (_, _) => Refresh();
        Root.SizeChanged += (_, _) => UpdateOptionsLayout();
        refreshTimer = DispatcherQueue.CreateTimer();
        refreshTimer.Interval = TimeSpan.FromMilliseconds(500);
        refreshTimer.Tick += (_, _) => Refresh();
        pasteTimer = DispatcherQueue.CreateTimer();
        pasteTimer.Interval = TimeSpan.FromMilliseconds(450);
        pasteTimer.IsRepeating = false;
        pasteTimer.Tick += async (_, _) => await ParseAsync();
        QueueList.ItemsSource = taskRows;
        var focusLink = new KeyboardAccelerator { Key = VirtualKey.L, Modifiers = VirtualKeyModifiers.Control };
        focusLink.Invoked += (_, e) => { Navigation.SelectedItem = ParseNav; LinkInput.Focus(FocusState.Keyboard); LinkInput.SelectAll(); e.Handled = true; };
        Root.KeyboardAccelerators.Add(focusLink);
    }
    private async void Loaded(object sender, RoutedEventArgs e)
    {
        Root.Loaded -= Loaded;
        var scale = Root.XamlRoot.RasterizationScale;
        var workArea = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var width = (int)Math.Min(1080 * scale, workArea.Width * 0.9);
        var height = (int)Math.Min(720 * scale, workArea.Height * 0.9);
        AppWindow.MoveAndResize(new Windows.Graphics.RectInt32(
            workArea.X + (workArea.Width - width) / 2, workArea.Y + (workArea.Height - height) / 2, width, height));
        Navigation.IsEnabled = false;
        StatusLine.Text = "正在读取设置与下载记录";
        try
        {
            runtime = await Task.Run(() => new AppRuntime(profile));
            LoadSettings();
            ModeSelect.SelectedIndex = (int)runtime.Config.DefaultDownloadMode;
            Destination.Text = runtime.Config.SavePath;
            ApplyTheme();
            AccountLabel.Text = runtime.Auth.IsLoggedIn ? "已登录" : "扫码登录";
            initialized = true;
            Navigation.IsEnabled = true;
            Navigation.SelectedItem = ParseNav;
            refreshTimer.Start();
            Refresh();
            AppWindow.Show();
        }
        catch (Exception error)
        {
            StatusLine.Text = "启动失败";
            ShowNotice("启动失败，请检查用户数据目录权限与文件格式。" + Network.SafeError(error), true);
        }
    }
    public void ShowWindow()
    {
        AppWindow.Show();
        if (AppWindow.Presenter is OverlappedPresenter presenter) presenter.Restore();
        Activate();
    }
    private void NavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (!initialized || args.SelectedItem is not NavigationViewItem item) return;
        currentPage = item.Tag?.ToString() ?? "parse";
        ParsePage.Visibility = currentPage == "parse" ? Visibility.Visible : Visibility.Collapsed;
        QueuePage.Visibility = currentPage == "queue" ? Visibility.Visible : Visibility.Collapsed;
        SettingsPage.Visibility = currentPage == "settings" ? Visibility.Visible : Visibility.Collapsed;
        LogsPage.Visibility = currentPage == "logs" ? Visibility.Visible : Visibility.Collapsed;
        PageTitle.Text = item.Content?.ToString();
        PageSubtitle.Text = currentPage switch
        {
            "queue" => "查看进度，打开已保存的文件", "settings" => "默认下载方式与应用偏好", "logs" => "查看解析和下载过程中的运行信息", _ => "粘贴分享链接，选择需要保存的内容"
        };
        Notice.IsOpen = false;
        Refresh();
    }
    private void ShowNotice(string message, bool error = false)
    {
        Notice.Message = message;
        Notice.Severity = error ? InfoBarSeverity.Error : InfoBarSeverity.Informational;
        Notice.IsOpen = true;
    }
    private void LinkChanged(object sender, TextChangedEventArgs e)
    {
        if (programmaticLink is not null && LinkInput.Text == programmaticLink) { programmaticLink = null; return; }
        programmaticLink = null;
        if (!initialized || applyingMedia || adding) return;
        pasteTimer.Stop();
        parseCancellation?.Cancel();
        qualityCancellation?.Cancel();
        media = null;
        MediaPanel.Visibility = Visibility.Collapsed;
        ParseEmpty.Visibility = Visibility.Visible;
        AddButton.IsEnabled = false;
        destinationOverride = null;
        Destination.Text = runtime!.Config.SavePath;
        try { BilibiliApi.ExtractUrl(LinkInput.Text); pasteTimer.Start(); }
        catch (InvalidDataException) { }
    }
    private async void LinkKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter) { e.Handled = true; pasteTimer.Stop(); await ParseAsync(); }
    }
    private async void ParseClicked(object sender, RoutedEventArgs e) { pasteTimer.Stop(); await ParseAsync(); }
    private async void PasteClicked(object sender, RoutedEventArgs e)
    {
        try
        {
            var clipboard = Clipboard.GetContent();
            if (clipboard.Contains(StandardDataFormats.Text)) LinkInput.Text = await clipboard.GetTextAsync();
            else ShowNotice("剪贴板中没有文本链接");
        }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private async Task ParseAsync(DownloadRecord? reselect = null)
    {
        if (runtime is null || adding || closing) return;
        parseCancellation?.Cancel();
        var cancellation = new CancellationTokenSource();
        parseCancellation = cancellation;
        qualityCancellation?.Cancel();
        ParseProgress.Visibility = Visibility.Visible;
        ParseButton.IsEnabled = false;
        AddButton.IsEnabled = false;
        Notice.IsOpen = false;
        try
        {
            var result = await runtime.Api.ParseAsync(LinkInput.Text, cancellation.Token);
            if (cancellation.IsCancellationRequested) return;
            var selected = reselect is null ? result.DefaultEpisodeIndex : result.Episodes.FindIndex(ep => ep.Cid == reselect.Episode.Cid && ep.Bvid == reselect.Episode.Bvid);
            if (selected < 0) throw new InvalidDataException("原任务的分集已不可用，请重新解析并选择分集");
            media = result;
            destinationOverride = reselect?.SaveDir;
            applyingMedia = true;
            MediaTitle.Text = result.Title;
            ToolTipService.SetToolTip(MediaTitle, result.Title);
            MediaSource.Text = result.Episodes.Count > 1 ? $"{result.SourceName} · 共 {result.Episodes.Count} 集" : result.SourceName;
            var episodes = result.Episodes.Select((episode, index) =>
            {
                var prefix = result.Title + " - ";
                var displayTitle = episode.Title.StartsWith(prefix, StringComparison.Ordinal) ? episode.Title[prefix.Length..] : episode.Title;
                return new EpisodeRow(index, episode, displayTitle);
            }).ToList();
            EpisodeList.ItemsSource = episodes;
            EpisodeList.SelectedItems.Add(episodes[selected]);
            EpisodePanel.Visibility = episodes.Count > 1 ? Visibility.Visible : Visibility.Collapsed;
            EpisodeColumn.Width = episodes.Count > 1 ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
            UpdateOptionsLayout();
            if (reselect is not null) ModeSelect.SelectedIndex = (int)reselect.DownloadMode;
            MediaPanel.Visibility = Visibility.Visible;
            ParseEmpty.Visibility = Visibility.Collapsed;
            UpdateDestination();
            applyingMedia = false;
            UpdatePreview();
            await LoadQualitiesAsync(reselect?.Quality.Qn);
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception ex)
        {
            if (!cancellation.IsCancellationRequested)
            {
                media = null; AddButton.IsEnabled = false;
                MediaPanel.Visibility = Visibility.Collapsed; ParseEmpty.Visibility = Visibility.Visible;
                ShowNotice(Network.SafeError(ex), true);
            }
        }
        finally
        {
            applyingMedia = false;
            if (ReferenceEquals(parseCancellation, cancellation)) { ParseProgress.Visibility = Visibility.Collapsed; ParseButton.IsEnabled = true; }
            cancellation.Dispose();
            if (ReferenceEquals(parseCancellation, cancellation)) parseCancellation = null;
        }
    }
    private async void ModeChanged(object sender, SelectionChangedEventArgs e)
    {
        if (initialized && !applyingMedia) await LoadQualitiesAsync();
    }
    private async void RefreshQualities(object sender, RoutedEventArgs e) => await LoadQualitiesAsync((QualitySelect.SelectedItem as Quality)?.Qn);
    private async Task LoadQualitiesAsync(int? preferred = null)
    {
        if (runtime is null || media is null || adding) return;
        var episode = EpisodeList.SelectedItems.Cast<EpisodeRow>().OrderBy(r => r.Index).FirstOrDefault()?.Episode;
        if (episode is null) { AddButton.IsEnabled = false; return; }
        qualityCancellation?.Cancel();
        var cancellation = new CancellationTokenSource();
        qualityCancellation = cancellation;
        qualityEpisodeCid = episode.Cid;
        var mode = Mode;
        preferred ??= qualityMemory.GetValueOrDefault(mode, 0);
        QualityLabel.Text = mode == DownloadMode.Audio ? "音频品质" : "画质";
        QualitySelect.ItemsSource = null;
        QualitySelect.IsEnabled = false;
        QualityNote.Text = "正在读取当前账号可下载的品质…";
        AddButton.IsEnabled = false;
        try
        {
            var options = await runtime.Api.QualitiesAsync(episode, mode, cancellation.Token);
            if (cancellation.IsCancellationRequested) return;
            QualitySelect.ItemsSource = options;
            QualitySelect.SelectedItem = options.FirstOrDefault(q => q.Qn == preferred) ?? options.FirstOrDefault();
            QualitySelect.IsEnabled = true;
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception ex) { if (!cancellation.IsCancellationRequested) { QualityNote.Text = Network.SafeError(ex); ShowNotice(QualityNote.Text, true); } }
        finally
        {
            cancellation.Dispose();
            if (ReferenceEquals(qualityCancellation, cancellation)) qualityCancellation = null;
        }
    }
    private void QualityChanged(object sender, SelectionChangedEventArgs e)
    {
        if (QualitySelect.SelectedItem is Quality quality) { qualityMemory[Mode] = quality.Qn; QualityNote.Text = quality.AvailabilityNote; }
        UpdatePreview();
    }
    private async void EpisodeSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (applyingMedia || !initialized) return;
        UpdatePreview();
        var episode = EpisodeList.SelectedItems.Cast<EpisodeRow>().OrderBy(r => r.Index).FirstOrDefault();
        if (episode is not null && episode.Episode.Cid != qualityEpisodeCid) await LoadQualitiesAsync((QualitySelect.SelectedItem as Quality)?.Qn);
    }
    private void SelectAllEpisodes(object sender, RoutedEventArgs e) => EpisodeList.SelectAll();
    private void InvertEpisodes(object sender, RoutedEventArgs e)
    {
        var selected = EpisodeList.SelectedItems.Cast<EpisodeRow>().ToHashSet();
        applyingMedia = true;
        EpisodeList.SelectedItems.Clear();
        foreach (var item in EpisodeList.Items.Cast<EpisodeRow>()) if (!selected.Contains(item)) EpisodeList.SelectedItems.Add(item);
        applyingMedia = false;
        EpisodeSelectionChanged(sender, null!);
    }
    private void UpdatePreview()
    {
        if (runtime is null || media is null) return;
        var selected = EpisodeList.SelectedItems.Cast<EpisodeRow>().OrderBy(r => r.Index).ToList();
        EpisodeCount.Text = $"分集  {selected.Count} / {media.Episodes.Count}";
        AddButton.IsEnabled = selected.Count > 0 && QualitySelect.SelectedItem is Quality && !adding;
        if (QualitySelect.SelectedItem is not Quality quality || selected.Count == 0) { FilenamePreview.Text = "选择分集和品质后显示文件名"; return; }
        FilenamePreview.Text = string.Join(Environment.NewLine, selected.Take(5).Select(r => Naming.Render(runtime.Config.FilenameTemplate, media, r.Episode, quality, r.Index + 1) + Display.Extension(Mode)));
        if (selected.Count > 5) FilenamePreview.Text += $"\n另有 {selected.Count - 5} 个文件";
    }
    private void UpdateOptionsLayout()
    {
        var sideBySide = media?.Episodes.Count == 1 && Root.ActualWidth >= 900;
        QualityColumn.Width = sideBySide ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
        DownloadOptions.ColumnSpacing = sideBySide ? 16 : 0;
        Grid.SetRow(QualityOptions, sideBySide ? 0 : 1);
        Grid.SetColumn(QualityOptions, sideBySide ? 1 : 0);
        Grid.SetRow(FilenameOptions, sideBySide ? 1 : 2);
        Grid.SetColumnSpan(FilenameOptions, sideBySide ? 2 : 1);
    }
    private void UpdateDestination()
    {
        if (runtime is null) return;
        Destination.Text = destinationOverride ?? (runtime.Config.OrganizeBySource && media is not null ? Path.Combine(runtime.Config.SavePath, media.FolderTitle) : runtime.Config.SavePath);
        ToolTipService.SetToolTip(Destination, Destination.Text);
    }
    private async Task<string?> PickFolderAsync()
    {
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.Downloads };
        picker.FileTypeFilter.Add("*");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
        return (await picker.PickSingleFolderAsync())?.Path;
    }
    private async void ChooseDestination(object sender, RoutedEventArgs e)
    {
        DestinationButton.IsEnabled = false;
        var original = media;
        try { var path = await PickFolderAsync(); if (path is not null && ReferenceEquals(original, media)) { destinationOverride = path; UpdateDestination(); } }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
        finally { DestinationButton.IsEnabled = true; }
    }
    private async void AddClicked(object sender, RoutedEventArgs e)
    {
        if (runtime is null || media is null || adding || QualitySelect.SelectedItem is not Quality quality) return;
        var selected = EpisodeList.SelectedItems.Cast<EpisodeRow>().OrderBy(r => r.Index).ToList();
        if (selected.Count == 0) return;
        adding = true;
        EpisodeList.IsEnabled = false; ModeSelect.IsEnabled = false; QualitySelect.IsEnabled = false;
        LinkInput.IsEnabled = false; DestinationButton.IsEnabled = false; AddButton.IsEnabled = false; ParseButton.IsEnabled = false;
        try
        {
            var duplicates = runtime.Downloads.CountDuplicates(selected.Select(r => r.Episode), quality, Mode);
            if (duplicates > 0 && !await ConfirmAsync("重复下载", $"队列或历史中已有 {duplicates} 个相同分集、模式与品质的任务。继续添加会使用独立文件名。", "继续下载")) return;
            var specs = selected.Select(r => new TaskSpec(media.SourceUrl, r.Episode, quality, Destination.Text,
                Naming.Render(runtime.Config.FilenameTemplate, media, r.Episode, quality, r.Index + 1), Mode)).ToList();
            var count = await Task.Run(() => runtime.Downloads.Add(specs).Count);
            QueueFilter.SelectedIndex = 0; QueueSearch.Text = "";
            resetQueueScroll = true;
            Navigation.SelectedItem = QueueNav;
            ShowNotice($"已添加 {count} 个下载任务");
            Refresh();
        }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
        finally
        {
            adding = false;
            EpisodeList.IsEnabled = true; ModeSelect.IsEnabled = true; QualitySelect.IsEnabled = true;
            LinkInput.IsEnabled = true; DestinationButton.IsEnabled = true; ParseButton.IsEnabled = true;
            UpdatePreview();
        }
    }
    private async Task<bool> ConfirmAsync(string title, string content, string primary)
    {
        if (dialogOpen) return false;
        dialogOpen = true;
        try
        {
            var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, RequestedTheme = Root.RequestedTheme, Title = title, Content = content, PrimaryButtonText = primary, CloseButtonText = "取消", DefaultButton = ContentDialogButton.Close };
            return await dialog.ShowAsync() == ContentDialogResult.Primary;
        }
        finally { dialogOpen = false; }
    }
    private void Refresh()
    {
        if (runtime is null || closing) return;
        var metrics = runtime.Downloads.Metrics();
        StatusLine.Text = metrics.Active + metrics.Queued > 0
            ? $"{metrics.Active} 个进行中" + (metrics.Queued > 0 ? $"  ·  {metrics.Queued} 个排队" : "") + $"  ·  {Display.Bytes(metrics.SpeedBps)}/s  ·  已完成 {metrics.Completed}"
            : "准备就绪";
        if (runtime.Downloads.PersistenceError.Length > 0) ShowNotice(runtime.Downloads.PersistenceError, true);
        if (currentPage == "logs")
        {
            var logs = runtime.Logs.Reverse().ToArray();
            if (LogList.ItemsSource is not string[] displayed || !logs.SequenceEqual(displayed)) LogList.ItemsSource = logs;
        }
        if (currentPage != "queue") return;
        var snapshot = runtime.Downloads.Snapshot();
        var ids = snapshot.Select(r => r.TaskId).ToHashSet();
        foreach (var id in rows.Keys.Where(id => !ids.Contains(id)).ToList()) { rows.Remove(id); selectedTasks.Remove(id); }
        foreach (var record in snapshot)
            if (rows.TryGetValue(record.TaskId, out var row)) row.Update(record, Root.ActualTheme); else rows.Add(record.TaskId, new TaskRow(record, Root.ActualTheme));
        var search = QueueSearch.Text.Trim();
        var filter = QueueFilter.SelectedIndex;
        var visible = snapshot.Where(r => (filter switch { 1 => !r.IsTerminal, 2 => r.Status == DownloadStatus.Completed, 3 => r.CanRetry, _ => true }) &&
            (search.Length == 0 || r.Title.Contains(search, StringComparison.OrdinalIgnoreCase) || r.OutputPath.Contains(search, StringComparison.OrdinalIgnoreCase)))
            .OrderBy(r => r.IsTerminal ? 1 : 0).ThenByDescending(r => r.CreatedAt).Select(r => rows[r.TaskId]).ToList();
        syncingQueue = true;
        try
        {
            if (!taskRows.SequenceEqual(visible))
            {
                var firstVisible = (QueueList.ItemsPanelRoot as ItemsStackPanel)?.FirstVisibleIndex ?? 0;
                taskRows.ReplaceWith(visible);
                if (!resetQueueScroll && taskRows.Count > 0)
                    QueueList.ScrollIntoView(taskRows[Math.Clamp(firstVisible, 0, taskRows.Count - 1)], ScrollIntoViewAlignment.Leading);
            }
            if (resetQueueScroll && taskRows.Count > 0) QueueList.ScrollIntoView(taskRows[0], ScrollIntoViewAlignment.Leading);
            resetQueueScroll = false;
            foreach (var row in taskRows)
            {
                if (selectedTasks.Contains(row.Id) && !QueueList.SelectedItems.Contains(row)) QueueList.SelectedItems.Add(row);
                if (!selectedTasks.Contains(row.Id) && QueueList.SelectedItems.Contains(row)) QueueList.SelectedItems.Remove(row);
            }
        }
        finally { syncingQueue = false; }
        SelectedCount.Text = $"已选 {selectedTasks.Count} 项";
        BatchButton.IsEnabled = selectedTasks.Count > 0;
        QueueEmpty.Visibility = visible.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        QueueEmptyTitle.Text = snapshot.Count == 0 ? "这里还没有任务" : "没有匹配的任务";
    }
    private void QueueFilterChanged(object sender, SelectionChangedEventArgs e) { if (initialized) { resetQueueScroll = true; Refresh(); } }
    private void QueueSearchChanged(object sender, TextChangedEventArgs e) { if (initialized) { resetQueueScroll = true; Refresh(); } }
    private void QueueSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (syncingQueue) return;
        foreach (var row in e.AddedItems.Cast<TaskRow>()) selectedTasks.Add(row.Id);
        foreach (var row in e.RemovedItems.Cast<TaskRow>()) selectedTasks.Remove(row.Id);
        SelectedCount.Text = $"已选 {selectedTasks.Count} 项"; BatchButton.IsEnabled = selectedTasks.Count > 0;
    }
    private void SelectQueue(object sender, RoutedEventArgs e) => QueueList.SelectAll();
    private void DeselectQueue(object sender, RoutedEventArgs e) { selectedTasks.Clear(); QueueList.SelectedItems.Clear(); Refresh(); }
    private void CancelSelected(object sender, RoutedEventArgs e) { foreach (var id in selectedTasks) runtime!.Downloads.Cancel(id); Refresh(); }
    private void RetrySelected(object sender, RoutedEventArgs e) => RetryIds(selectedTasks.ToArray());
    private void RetryFailed(object sender, RoutedEventArgs e) => RetryIds(runtime!.Downloads.Snapshot().Where(r => r.CanRetry).Select(r => r.TaskId).ToArray());
    private void RetryIds(IEnumerable<string> ids)
    {
        try { var count = runtime!.Downloads.Retry(ids); QueueFilter.SelectedIndex = 0; QueueSearch.Text = ""; resetQueueScroll = true; Refresh(); ShowNotice($"已添加 {count} 个重试任务"); }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private async void RemoveSelected(object sender, RoutedEventArgs e)
    {
        var ids = selectedTasks.ToArray();
        if (await ConfirmAsync("移除任务记录", $"移除所选 {ids.Length} 条记录，正在运行的任务会保留。已下载文件会保留在保存目录。", "移除记录")) { runtime!.Downloads.Remove(ids); Refresh(); }
    }
    private async void ClearFinished(object sender, RoutedEventArgs e)
    {
        var ids = runtime!.Downloads.Snapshot().Where(r => r.IsTerminal).Select(r => r.TaskId).ToArray();
        if (ids.Length == 0) { ShowNotice("没有可清理的已结束任务"); return; }
        if (await ConfirmAsync("清理已结束任务", $"清理 {ids.Length} 条下载记录，已下载文件会保留在保存目录。", "清理记录")) { runtime.Downloads.Remove(ids); Refresh(); }
    }
    private static string TaskId(object sender) => (sender as FrameworkElement)?.Tag?.ToString() ?? "";
    private DownloadRecord? FindTask(object sender) => runtime?.Downloads.Snapshot().Find(r => r.TaskId == TaskId(sender));
    private void CancelTask(object sender, RoutedEventArgs e) { runtime!.Downloads.Cancel(TaskId(sender)); Refresh(); }
    private void RetryTask(object sender, RoutedEventArgs e) => RetryIds([TaskId(sender)]);
    private void RemoveTask(object sender, RoutedEventArgs e) { runtime!.Downloads.Remove([TaskId(sender)]); Refresh(); }
    private void OpenTask(object sender, RoutedEventArgs e) { if (FindTask(sender) is { } record) OpenPath(record.OutputPath); }
    private void LocateTask(object sender, RoutedEventArgs e)
    {
        if (FindTask(sender) is not { } record) return;
        try
        {
            if (!File.Exists(record.OutputPath)) { ShowNotice("文件已移动或删除", true); return; }
            var info = new ProcessStartInfo("explorer.exe") { UseShellExecute = true }; info.ArgumentList.Add("/select," + record.OutputPath); Process.Start(info);
        }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private async void ReselectTask(object sender, RoutedEventArgs e)
    {
        if (FindTask(sender) is not { } record || adding) return;
        Navigation.SelectedItem = ParseNav;
        if (LinkInput.Text != record.SourceUrl) { programmaticLink = record.SourceUrl; LinkInput.Text = record.SourceUrl; }
        pasteTimer.Stop();
        await ParseAsync(record);
    }
    private void GoParse(object sender, RoutedEventArgs e) { Navigation.SelectedItem = ParseNav; LinkInput.Focus(FocusState.Keyboard); }
    private void OpenPath(string path)
    {
        try
        {
            if (!File.Exists(path) && !Directory.Exists(path)) throw new FileNotFoundException("文件或目录已移动或删除");
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private void OpenLogs(object sender, RoutedEventArgs e) => OpenPath(Path.Combine(runtime!.Storage.DirectoryPath, "logs"));
    private void LoadSettings()
    {
        var config = runtime!.Config;
        SettingDirectory.Text = config.SavePath; SettingTemplate.Text = config.FilenameTemplate; SettingFFmpeg.Text = config.FfmpegPath;
        SettingMode.SelectedIndex = (int)config.DefaultDownloadMode; SettingConcurrent.Value = config.MaxConcurrentTasks; SettingSpeed.Value = config.SpeedLimitMb;
        SettingOrganize.IsOn = config.OrganizeBySource; SettingCleanup.IsOn = config.CleanupTempFiles;
        SettingAnimations.IsOn = config.AnimationsEnabled; SettingTheme.SelectedIndex = config.AppearanceMode switch { "light" => 1, "dark" => 2, _ => 0 };
    }
    private async void ChooseDefaultDirectory(object sender, RoutedEventArgs e)
    {
        try { var path = await PickFolderAsync(); if (path is not null) SettingDirectory.Text = path; }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private async void ChooseFFmpeg(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FileOpenPicker(); picker.FileTypeFilter.Add(".exe"); InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this));
            var file = await picker.PickSingleFileAsync(); if (file is not null) SettingFFmpeg.Text = file.Path;
        }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private void SaveSettings(object sender, RoutedEventArgs e)
    {
        try
        {
            if (!double.IsFinite(SettingConcurrent.Value) || !double.IsFinite(SettingSpeed.Value)) throw new InvalidDataException("请填写有效的并发数与限速值");
            var config = runtime!.Config with
            {
                SavePath = Path.GetFullPath(SettingDirectory.Text.Trim()), FilenameTemplate = SettingTemplate.Text.Trim() is { Length: > 0 } template ? template : "{title}",
                FfmpegPath = SettingFFmpeg.Text.Trim(), DefaultDownloadMode = (DownloadMode)SettingMode.SelectedIndex,
                MaxConcurrentTasks = Math.Clamp((int)SettingConcurrent.Value, 1, 16), SpeedLimitMb = Math.Max(0, SettingSpeed.Value),
                OrganizeBySource = SettingOrganize.IsOn, CleanupTempFiles = SettingCleanup.IsOn,
                AppearanceMode = SettingTheme.SelectedIndex switch { 1 => "light", 2 => "dark", _ => "system" }, AnimationsEnabled = SettingAnimations.IsOn
            };
            FFmpeg.Resolve(config.FfmpegPath);
            runtime.SaveConfig(config); ApplyTheme(); UpdateDestination(); UpdatePreview(); ShowNotice("设置已保存");
        }
        catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
    }
    private void ApplyTheme()
    {
        Root.RequestedTheme = runtime!.Config.AppearanceMode switch { "light" => ElementTheme.Light, "dark" => ElementTheme.Dark, _ => ElementTheme.Default };
        Navigation.ContentTransitions = runtime.Config.AnimationsEnabled ? new TransitionCollection { new EntranceThemeTransition() } : [];
    }
    private async void AccountClicked(object sender, RoutedEventArgs e)
    {
        if (runtime is null || dialogOpen) return;
        if (runtime.Auth.IsLoggedIn)
        {
            try
            {
                if (await ConfirmAsync("账号已登录", "登录凭证受当前 Windows 用户保护。退出后可重新扫码。", "退出登录"))
                { runtime.Auth.SetSessdata(""); AccountLabel.Text = "扫码登录"; qualityMemory.Clear(); await LoadQualitiesAsync(); }
            }
            catch (Exception ex) { ShowNotice(Network.SafeError(ex), true); }
            return;
        }
        dialogOpen = true;
        qrCancellation = new CancellationTokenSource();
        var cancellation = qrCancellation;
        var image = new Image { Width = 220, Height = 220, HorizontalAlignment = HorizontalAlignment.Center };
        var status = new TextBlock { Text = "正在申请二维码…", TextWrapping = TextWrapping.Wrap, HorizontalAlignment = HorizontalAlignment.Center };
        var panel = new StackPanel { Spacing = 16, MinWidth = 300 }; panel.Children.Add(image); panel.Children.Add(status);
        var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, RequestedTheme = Root.RequestedTheme, Title = "使用哔哩哔哩扫码登录", Content = panel, CloseButtonText = "关闭" };
        dialog.Closed += (_, _) => cancellation.Cancel();
        async Task PollAsync()
        {
            try
            {
                var qr = await runtime.Auth.CreateQrAsync(cancellation.Token);
                using var generator = new QRCodeGenerator();
                using var data = generator.CreateQrCode(qr.Url, QRCodeGenerator.ECCLevel.M);
                using var png = new PngByteQRCode(data);
                using var stream = new InMemoryRandomAccessStream();
                using (var writer = new DataWriter(stream.GetOutputStreamAt(0))) { writer.WriteBytes(png.GetGraphic(8)); await writer.StoreAsync(); }
                stream.Seek(0);
                var bitmap = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(); await bitmap.SetSourceAsync(stream); image.Source = bitmap;
                status.Text = "请使用哔哩哔哩 App 扫码";
                while (!cancellation.IsCancellationRequested)
                {
                    await Task.Delay(2000, cancellation.Token);
                    var poll = await runtime.Auth.PollQrAsync(qr, cancellation.Token);
                    status.Text = poll.Code switch { 86101 => "等待扫码", 86090 => "已扫码，请在手机上确认", 86038 => "二维码已过期，请关闭后重新打开", 0 => "登录成功", _ => poll.Message };
                    if (poll.Code == 0) { AccountLabel.Text = "已登录"; dialog.Hide(); return; }
                    if (poll.Code == 86038) return;
                }
            }
            catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
            catch (Exception ex) { status.Text = Network.SafeError(ex); }
        }
        var polling = PollAsync();
        try { await dialog.ShowAsync(); await polling; }
        finally { cancellation.Cancel(); cancellation.Dispose(); qrCancellation = null; dialogOpen = false; }
        if (runtime.Auth.IsLoggedIn) { qualityMemory.Clear(); await LoadQualitiesAsync(); }
    }
    private async void WindowClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (allowClose) return;
        args.Cancel = true;
        if (closing || dialogOpen) return;
        if (runtime?.Downloads.Snapshot().Any(r => !r.IsTerminal) == true)
        {
            dialogOpen = true;
            var dialog = new ContentDialog { XamlRoot = Root.XamlRoot, RequestedTheme = Root.RequestedTheme, Title = "下载仍在进行", Content = "可以最小化窗口继续下载，或停止任务并退出。", PrimaryButtonText = "最小化继续下载", SecondaryButtonText = "停止并退出", CloseButtonText = "返回", DefaultButton = ContentDialogButton.Primary };
            var result = await dialog.ShowAsync(); dialogOpen = false;
            if (result == ContentDialogResult.Primary) { (AppWindow.Presenter as OverlappedPresenter)?.Minimize(); return; }
            if (result != ContentDialogResult.Secondary) return;
        }
        closing = true; refreshTimer.Stop(); pasteTimer.Stop(); parseCancellation?.Cancel(); qualityCancellation?.Cancel(); qrCancellation?.Cancel();
        Navigation.IsEnabled = false; StatusLine.Text = "正在保存记录并退出";
        try { if (runtime is not null) await runtime.DisposeAsync(); }
        catch (Exception ex) { runtime?.Log(Network.SafeError(ex)); }
        allowClose = true; Close();
    }
}
