using System.Collections.Concurrent;

namespace BiliDownloader.Core;

public sealed class AppRuntime : IAsyncDisposable
{
    public Storage Storage { get; }
    public AppConfig Config { get; private set; }
    public Network Network { get; }
    public AuthService Auth { get; }
    public BilibiliApi Api { get; }
    public DownloadManager Downloads { get; }
    private readonly ConcurrentQueue<string> logs = new();
    private readonly object logLock = new();
    public string[] Logs => logs.ToArray();
    public AppRuntime(string? profile = null, Network? network = null)
    {
        Network = network ?? new Network();
        Storage = new Storage(profile);
        Config = Storage.LoadConfig();
        Auth = new AuthService(Network, Storage);
        try { Auth.Load(); }
        catch (Exception ex) { Log("旧登录态读取失败，请重新扫码 " + Network.SafeError(ex)); }
        Storage.BackupForMigration();
        var backup = new Storage(Path.Combine(Storage.DirectoryPath, "backup-before-winui3"));
        if (File.Exists(backup.AuthPath))
        {
            try { new AuthService(Network, backup).Load(); }
            catch (Exception ex) { Log("账号备份读取失败 " + Network.SafeError(ex)); }
        }
        Api = new BilibiliApi(Network, () => Auth.Cookies);
        Downloads = new DownloadManager(Api, Network, Auth, Storage, Config, Log);
        Log("C# / WinUI 3 客户端已就绪");
    }
    public void SaveConfig(AppConfig config)
    {
        Directory.CreateDirectory(config.SavePath);
        Storage.SaveConfig(config);
        Config = config;
        Downloads.UpdateConfig(config);
        Log("设置已保存");
    }
    public void Log(string message)
    {
        var safe = Network.SafeError(new Exception(message));
        var line = $"{DateTime.Now:HH:mm:ss}  {safe}";
        logs.Enqueue(line);
        while (logs.Count > 1000) logs.TryDequeue(out _);
        try
        {
            lock (logLock)
            {
                var directory = Path.Combine(Storage.DirectoryPath, "logs");
                Directory.CreateDirectory(directory);
                File.AppendAllText(Path.Combine(directory, $"native-{DateTime.Now:yyyy-MM-dd}.log"), line + Environment.NewLine);
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
    public async ValueTask DisposeAsync() { await Downloads.DisposeAsync(); Network.Dispose(); }
}
