using System.Text.Json.Serialization;

namespace BiliDownloader.Core;

public enum DownloadMode { Combined, Audio, Video }
public enum DownloadStatus { Queued, Preparing, Downloading, Merging, Completed, Failed, Canceled }

public sealed record Episode(string Title, string Bvid, long Cid, bool IsBangumi);
public sealed record Quality(int Qn, string Label, string AvailabilityNote = "");
public sealed record MediaResult(string SourceUrl, string NormalizedUrl, string Title,
    List<Episode> Episodes, int DefaultEpisodeIndex = 0, string FolderTitle = "", string SourceName = "");
public sealed record MediaStream(string Kind, List<Uri> Urls, string Referer, long Size = 0);
public sealed record StreamSet(MediaStream? Video, MediaStream? Audio);
public sealed record TaskSpec(string SourceUrl, Episode Episode, Quality Quality, string SaveDir,
    string FilenameStem, DownloadMode DownloadMode);

public sealed record AppConfig
{
    public string SavePath { get; init; } = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads", "BiliDownloader");
    public bool OrganizeBySource { get; init; }
    public string FilenameTemplate { get; init; } = "{title}";
    public bool CleanupTempFiles { get; init; } = true;
    public double SpeedLimitMb { get; init; }
    public int MaxConcurrentTasks { get; init; } = 2;
    public string FfmpegPath { get; init; } = "ffmpeg";
    public string AppearanceMode { get; init; } = "system";
    public bool AnimationsEnabled { get; init; } = true;
    public DownloadMode DefaultDownloadMode { get; init; }
}

public sealed record ProgressInfo
{
    public long TotalBytes { get; set; }
    public long DownloadedBytes { get; set; }
    public double Percent { get; set; }
    public long VideoTotalBytes { get; set; }
    public long VideoDownloadedBytes { get; set; }
    public long AudioTotalBytes { get; set; }
    public long AudioDownloadedBytes { get; set; }
}

// The on-disk schema deliberately matches the Python client, including Unix timestamps.
public sealed record DownloadRecord
{
    public string TaskId { get; init; } = Guid.NewGuid().ToString("N");
    public string SourceUrl { get; init; } = "";
    public string OutputPath { get; set; } = "";
    public string SaveDir { get; init; } = "";
    public string Title { get; init; } = "";
    public DownloadMode DownloadMode { get; init; }
    public DownloadStatus Status { get; set; } = DownloadStatus.Queued;
    public string Message { get; set; } = "等待开始";
    public string Error { get; set; } = "";
    public double CreatedAt { get; init; } = Now();
    public double? StartedAt { get; set; }
    public double? FinishedAt { get; set; }
    public Episode Episode { get; init; } = new("", "", 0, false);
    public Quality Quality { get; init; } = new(0, "默认画质");
    public ProgressInfo Progress { get; init; } = new();
    [JsonIgnore] public double SpeedBps { get; set; }
    [JsonIgnore] public bool IsTerminal => Status is DownloadStatus.Completed or DownloadStatus.Failed or DownloadStatus.Canceled;
    [JsonIgnore] public bool CanRetry => Status is DownloadStatus.Failed or DownloadStatus.Canceled;
    public static double Now() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
    public DownloadRecord Snapshot() => this with { Progress = Progress with { } };
}

public static class Display
{
    public static string Mode(DownloadMode mode) => mode switch { DownloadMode.Audio => "仅音频", DownloadMode.Video => "无声视频", _ => "完整视频" };
    public static string Extension(DownloadMode mode) => mode == DownloadMode.Audio ? ".m4a" : ".mp4";
    public static string Status(DownloadStatus status) => status switch
    {
        DownloadStatus.Queued => "等待中", DownloadStatus.Preparing => "准备中", DownloadStatus.Downloading => "下载中",
        DownloadStatus.Merging => "封装中", DownloadStatus.Completed => "已完成", DownloadStatus.Failed => "失败", _ => "已取消"
    };
    public static string Bytes(double bytes) => bytes switch
    {
        >= 1073741824 => $"{bytes / 1073741824:0.0} GiB", >= 1048576 => $"{bytes / 1048576:0.0} MiB",
        >= 1024 => $"{bytes / 1024:0.0} KiB", _ => $"{Math.Max(0, bytes):0} B"
    };
}
