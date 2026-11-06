using System.ComponentModel;
using System.Collections.ObjectModel;
using System.Collections.Specialized;
using BiliDownloader.Core;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace BiliDownloader.WinUI;

internal sealed class TaskRowCollection : ObservableCollection<TaskRow>
{
    public void ReplaceWith(IReadOnlyList<TaskRow> rows)
    {
        CheckReentrancy();
        Items.Clear();
        foreach (var row in rows) Items.Add(row);
        OnPropertyChanged(new PropertyChangedEventArgs(nameof(Count)));
        OnPropertyChanged(new PropertyChangedEventArgs("Item[]"));
        OnCollectionChanged(new NotifyCollectionChangedEventArgs(NotifyCollectionChangedAction.Reset));
    }
}

public sealed record EpisodeRow(int Index, Episode Episode, string DisplayTitle)
{
    public string Title => Episode.Title;
}

public sealed class TaskRow(DownloadRecord record, ElementTheme theme) : INotifyPropertyChanged
{
    private ElementTheme currentTheme = theme;
    public DownloadRecord Record { get; private set; } = record;
    public string Id => Record.TaskId;
    public string Title => Record.Title;
    public string OutputPath => Record.OutputPath;
    public string Meta => $"{Display.Mode(Record.DownloadMode)} · {Record.Quality.Label} · {Record.OutputPath}";
    public string StatusText => Display.Status(Record.Status);
    public SolidColorBrush StatusBrush => new((Record.Status, currentTheme == ElementTheme.Dark) switch
    {
        (DownloadStatus.Completed, false) => ColorHelper.FromArgb(255, 15, 123, 15),
        (DownloadStatus.Completed, true) => ColorHelper.FromArgb(255, 108, 203, 95),
        (DownloadStatus.Failed, false) => ColorHelper.FromArgb(255, 196, 43, 28),
        (DownloadStatus.Failed, true) => ColorHelper.FromArgb(255, 255, 153, 164),
        (_, false) => ColorHelper.FromArgb(255, 0, 103, 192),
        _ => ColorHelper.FromArgb(255, 96, 205, 255)
    });
    public double Percent => Record.Progress.Percent;
    public bool Indeterminate => Record.Status is DownloadStatus.Preparing or DownloadStatus.Merging || (Record.Status == DownloadStatus.Downloading && Record.Progress.TotalBytes <= 0);
    public Visibility ProgressVisibility => Record.IsTerminal ? Visibility.Collapsed : Visibility.Visible;
    public bool IsCompleted => Record.Status == DownloadStatus.Completed;
    public bool CanCancel => !Record.IsTerminal;
    public bool CanRetry => Record.CanRetry;
    public bool CanRemove => Record.IsTerminal || Record.Status == DownloadStatus.Queued;
    public string Detail => Record.Status switch
    {
        DownloadStatus.Completed => Display.Bytes(Record.Progress.TotalBytes > 0 ? Record.Progress.TotalBytes : Record.Progress.DownloadedBytes),
        DownloadStatus.Downloading => $"{Record.Progress.Percent:0.0}%  ·  {Display.Bytes(Record.SpeedBps)}/s  ·  {Display.Bytes(Record.Progress.DownloadedBytes)} / {Display.Bytes(Record.Progress.TotalBytes)}",
        _ => Record.Message
    };
    public void Update(DownloadRecord value, ElementTheme theme)
    {
        if (Record == value && currentTheme == theme) return;
        Record = value;
        currentTheme = theme;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(null));
    }
    public event PropertyChangedEventHandler? PropertyChanged;
}
