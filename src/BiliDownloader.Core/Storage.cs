using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace BiliDownloader.Core;

public sealed class Storage
{
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) }
    };
    private readonly object writeLock = new();
    public string DirectoryPath { get; }
    public string AuthPath => Path.Combine(DirectoryPath, "auth.json");
    public Storage(string? directory = null)
    {
        DirectoryPath = directory ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "BiliDownloader");
        Directory.CreateDirectory(DirectoryPath);
    }
    public AppConfig LoadConfig()
    {
        var config = Read<AppConfig>("config.json") ?? new();
        return config with
        {
            MaxConcurrentTasks = Math.Clamp(config.MaxConcurrentTasks, 1, 16),
            SpeedLimitMb = double.IsFinite(config.SpeedLimitMb) ? Math.Max(0, config.SpeedLimitMb) : 0,
            SavePath = string.IsNullOrWhiteSpace(config.SavePath) ? new AppConfig().SavePath : Path.GetFullPath(config.SavePath),
            FilenameTemplate = string.IsNullOrWhiteSpace(config.FilenameTemplate) ? "{title}" : config.FilenameTemplate
        };
    }
    public List<DownloadRecord> LoadTasks()
    {
        var result = new List<DownloadRecord>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var path = Path.Combine(DirectoryPath, "tasks.json");
        if (!File.Exists(path)) return result;
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        if (document.RootElement.ValueKind != JsonValueKind.Array) throw new InvalidDataException("下载记录格式无效");
        foreach (var element in document.RootElement.EnumerateArray())
        {
            try
            {
                var item = element.Deserialize<DownloadRecord>(Json);
                if (item is null || string.IsNullOrEmpty(item.TaskId) || !Regex.IsMatch(item.TaskId, @"\A[A-Za-z0-9_-]{1,64}\z") || item.Episode is null || item.Quality is null || item.Progress is null) continue;
                if (!seen.Add(item.TaskId)) continue;
                if (!item.IsTerminal)
                {
                    item.Status = DownloadStatus.Canceled;
                    item.Message = "上次退出时未完成，可重试";
                }
                result.Add(item);
            }
            catch (JsonException) { /* A damaged row must not hide other history. */ }
        }
        return result;
    }
    public void SaveConfig(AppConfig config) => Write("config.json", config);
    public void SaveTasks(IEnumerable<DownloadRecord> tasks) => Write("tasks.json", tasks);
    public void Write<T>(string filename, T value)
    {
        lock (writeLock) AtomicWrite(Path.Combine(DirectoryPath, filename), JsonSerializer.Serialize(value, Json));
    }
    private T? Read<T>(string filename)
    {
        var path = Path.Combine(DirectoryPath, filename);
        return File.Exists(path) ? JsonSerializer.Deserialize<T>(File.ReadAllText(path), Json) : default;
    }
    public void BackupForMigration()
    {
        var destination = Path.Combine(DirectoryPath, "backup-before-winui3");
        Directory.CreateDirectory(destination);
        foreach (var name in new[] { "config.json", "auth.json", "tasks.json" })
        {
            var source = Path.Combine(DirectoryPath, name);
            if (File.Exists(source) && !File.Exists(Path.Combine(destination, name))) File.Copy(source, Path.Combine(destination, name));
        }
    }
    public static void AtomicWrite(string path, string value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                var bytes = Encoding.UTF8.GetBytes(value);
                stream.Write(bytes);
                stream.Flush(true);
            }
            File.Move(temporary, path, true);
        }
        finally { File.Delete(temporary); }
    }
}
