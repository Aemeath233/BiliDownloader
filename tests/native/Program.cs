using System.Diagnostics;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using BiliDownloader.Core;

var workspace = Path.GetFullPath(args[0]);
var output = Path.GetFullPath(args[1]);
Directory.CreateDirectory(output);
var cases = new List<object>();
var failures = 0;
var generator = Path.Combine(workspace, "vendor", "ffmpeg", "bin", "ffmpeg.exe");
var probe = Path.Combine(workspace, "vendor", "ffmpeg", "bin", "ffprobe.exe");
var remux = Path.Combine(workspace, "vendor", "ffmpeg-minimal", "bin", "ffmpeg.exe");
var mediaDirectory = Path.Combine(output, "media");
Directory.CreateDirectory(mediaDirectory);
var videoPath = Path.Combine(mediaDirectory, "video.mp4");
var audioPath = Path.Combine(mediaDirectory, "audio.m4a");
await ProcessAsync(generator, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=5:d=0.4", "-an", "-c:v", "libx264", "-f", "mp4", videoPath);
await ProcessAsync(generator, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", "-vn", "-c:a", "aac", "-f", "mp4", audioPath);
var video = File.ReadAllBytes(videoPath);
var audio = File.ReadAllBytes(audioPath);
var muxedPath = Path.Combine(mediaDirectory, "muxed.mp4");
await ProcessAsync(generator, "-v", "error", "-y", "-i", videoPath, "-i", audioPath, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", muxedPath);

await TestAsync("输入与分享地址边界", async () =>
{
    Assert(BilibiliApi.ExtractUrl("分享 BV17F411T7Ao").EndsWith("BV17F411T7Ao"), "BV input");
    Assert(BilibiliApi.ExtractUrl("ep123").EndsWith("/ep123"), "episode input");
    foreach (var value in new[] { "https://bilibili.com.evil.invalid/video/BV17F411T7Ao", "https://user@www.bilibili.com/video/BV17F411T7Ao", "file:///etc/passwd", "https://127.0.0.1/" })
        await ThrowsAsync(() => Task.FromResult(Network.ShareUri(value)));
    Assert(Network.MediaUri("http://media.invalid/video").Scheme == "https", "media should upgrade to HTTPS");
});
await TestAsync("短链跳转允许列表", async () =>
{
    var calls = 0;
    using var network = new Network(new Handler((request, _) =>
    {
        calls++;
        return Task.FromResult(Redirect(request, "https://evil.invalid/video", HttpStatusCode.Redirect));
    }));
    await ThrowsAsync(() => network.SendAsync(new Uri("https://b23.tv/test"), shareOnly: true));
    Assert(calls == 1, "Untrusted redirect was contacted");
});
await TestAsync("番剧、短链与多 P 页码解析", async () =>
{
    using var network = new Network(new Handler((request, _) =>
    {
        if (request.RequestUri!.Host == "b23.tv") return Task.FromResult(Redirect(request, "https://www.bilibili.com/video/BV17F411T7Ao?p=2", HttpStatusCode.Redirect));
        if (request.RequestUri.AbsolutePath.Contains("pgc/view")) return Task.FromResult(Response(request, new { code = 0, result = new { season_title = "番剧演示", episodes = new[]
            { new { title = "1", long_title = "第一集", bvid = "BV17F411T7Ao", cid = 1, ep_id = 11 }, new { title = "2", long_title = "第二集", bvid = "BV17F411T7Ao", cid = 2, ep_id = 12 } } } }));
        return Task.FromResult(Response(request, new { code = 0, data = new { title = "多 P 演示", pages = new[] { new { page = 1, part = "第一节", cid = 1 }, new { page = 2, part = "第二节", cid = 2 } } } }));
    }));
    var api = new BilibiliApi(network, () => "");
    var bangumi = await api.ParseAsync("ep12");
    Assert(bangumi.DefaultEpisodeIndex == 1 && bangumi.Episodes.All(episode => episode.IsBangumi), "Bangumi episode selection failed");
    var shortLink = await api.ParseAsync("https://b23.tv/test");
    Assert(shortLink.DefaultEpisodeIndex == 1 && shortLink.NormalizedUrl.Contains("p=2"), "Short link lost its selected page");
});
await TestAsync("CDN 与非账号站点不接收 Cookie", async () =>
{
    using var network = new Network(new Handler((request, _) =>
    {
        Assert(!request.Headers.Contains("Cookie"), "Cookie leaked to CDN");
        return Task.FromResult(Response(request, new { code = 0 }));
    }));
    using var response = await network.SendAsync(new Uri("https://cdn.invalid/video"), "SESSDATA=test-secret");
});
await TestAsync("携带凭证的 POST 拒绝跨源重定向", async () =>
{
    var leaked = false;
    using var network = new Network(new Handler(async (request, ct) =>
    {
        if (request.RequestUri!.Host == "evil.invalid")
        {
            leaked = (await request.Content!.ReadAsStringAsync(ct)).Contains("private-refresh-token");
            return Response(request, new { code = 0 });
        }
        return Redirect(request, "https://evil.invalid/collect", HttpStatusCode.TemporaryRedirect);
    }));
    var rejected = false;
    try
    {
        using var response = await network.SendAsync(new Uri("https://passport.bilibili.com/refresh"), "SESSDATA=test", method: HttpMethod.Post,
            form: new() { ["refresh_token"] = "private-refresh-token" });
    }
    catch (InvalidDataException) { rejected = true; }
    Assert(rejected && !leaked, "Cross-origin redirect forwarded a secret form");
});
await TestAsync("账号请求拒绝 HTTPS 降级", async () =>
{
    var insecure = false;
    using var network = new Network(new Handler((request, _) =>
    {
        if (request.RequestUri!.Scheme == "http") { insecure = true; return Task.FromResult(Response(request, new { code = 0 })); }
        return Task.FromResult(Redirect(request, "http://passport.bilibili.com/collect", HttpStatusCode.Redirect));
    }));
    await ThrowsAsync(() => network.SendAsync(new Uri("https://passport.bilibili.com/account"), "SESSDATA=test"));
    Assert(!insecure, "HTTPS downgrade reached the network");
});
await TestAsync("接口缺少或包含无效状态码时拒绝成功判定", async () =>
{
    foreach (var body in new[] { "{}", "{\"code\":null}", "{\"code\":\"0\"}", "{\"code\":0.5}", "{\"code\":1e100}", "{\"code\":1}" })
    {
        using var json = JsonDocument.Parse(body);
        await ThrowsAsync(() => { Network.Check(json.RootElement); return Task.CompletedTask; });
    }
    using var success = JsonDocument.Parse("{\"code\":0}");
    Network.Check(success.RootElement);
});
await TestAsync("旧明文登录态立即迁移到 DPAPI", () =>
{
    var storage = NewStorage();
    storage.Write("auth.json", new { session = new AuthSession { Cookies = new() { ["SESSDATA"] = "legacy-test-secret" } } });
    using var network = new Network(new Handler((request, _) => Task.FromResult(Response(request, new { code = 0 }))));
    var auth = new AuthService(network, storage);
    auth.Load();
    Assert(auth.IsLoggedIn, "Legacy login lost");
    var stored = File.ReadAllText(storage.AuthPath);
    Assert(!stored.Contains("legacy-test-secret") && stored.Contains("dpapi-current-user"), "Legacy login remained plaintext");
    var reloaded = new AuthService(network, storage);
    reloaded.Load();
    Assert(reloaded.IsLoggedIn, "Encrypted login could not be reloaded");
    return Task.CompletedTask;
});
await TestAsync("迁移备份中的旧凭证也受 DPAPI 保护", async () =>
{
    var storage = NewStorage();
    storage.SaveConfig(Config(storage));
    var legacy = new { session = new AuthSession { Cookies = new() { ["SESSDATA"] = "backup-test-secret" } } };
    storage.Write("auth.json", legacy);
    var backup = new Storage(Path.Combine(storage.DirectoryPath, "backup-before-winui3"));
    backup.Write("auth.json", legacy);
    await using var runtime = new AppRuntime(storage.DirectoryPath, new Network(new Handler((request, _) => Task.FromResult(Response(request, new { code = 0 })))));
    Assert(!File.ReadAllText(backup.AuthPath).Contains("backup-test-secret") && File.ReadAllText(backup.AuthPath).Contains("dpapi-current-user"), "Backup retained plaintext credentials");
});
await TestAsync("敏感日志脱敏", () =>
{
    var error = Network.SafeError(new Exception("SESSDATA=test-cookie refresh_token=test-refresh https://cdn.invalid/file?sign=test-sign"));
    Assert(!error.Contains("test-cookie") && !error.Contains("test-refresh") && !error.Contains("test-sign"), "Secret in error message");
    return Task.CompletedTask;
});
foreach (var mode in Enum.GetValues<DownloadMode>())
    await TestAsync($"真实媒体下载与封装 {mode}", async () =>
    {
        var fixture = new MediaHandler(video, audio);
        var storage = NewStorage();
        using var network = new Network(fixture);
        var auth = new AuthService(network, storage);
        var api = new BilibiliApi(network, () => "");
        await using var manager = new DownloadManager(api, network, auth, storage, Config(storage), _ => { });
        var id = manager.Add([Spec(storage, mode)])[0];
        await WaitAsync(() => manager.Snapshot().Single().IsTerminal);
        var record = manager.Snapshot().Single();
        Assert(record.Status == DownloadStatus.Completed, record.Error);
        var types = await StreamTypesAsync(record.OutputPath);
        Assert(types.Contains("video") == (mode != DownloadMode.Audio) && types.Contains("audio") == (mode != DownloadMode.Video), "Wrong output tracks");
        Assert(!fixture.Paths.Contains("/audio") || mode != DownloadMode.Video, "Silent video fetched audio");
        Assert(!fixture.Paths.Contains("/video") || mode != DownloadMode.Audio, "Audio-only fetched video");
        Assert(Path.GetExtension(record.OutputPath) == Display.Extension(mode), "Wrong extension");
        var actualHashes = await PacketHashesAsync(record.OutputPath);
        foreach (var source in new[] { (Kind: "video", Path: videoPath), (Kind: "audio", Path: audioPath) })
            if (types.Contains(source.Kind)) Assert(actualHashes[source.Kind].SequenceEqual((await PacketHashesAsync(source.Path))[source.Kind]), "Remux changed encoded packets");
    });
foreach (var mode in new[] { DownloadMode.Audio, DownloadMode.Video })
    await TestAsync($"旧式合流提取 {mode}", async () =>
    {
        var storage = NewStorage();
        using var network = new Network(new MediaHandler(video, audio) { Muxed = File.ReadAllBytes(muxedPath) });
        var api = new BilibiliApi(network, () => "");
        var quality = (await api.QualitiesAsync(new("Test", "BV17F411T7Ao", 1, false), mode))[0];
        await using var manager = new DownloadManager(api, network, new(network, storage), storage, Config(storage), _ => { });
        manager.Add([Spec(storage, mode) with { Quality = quality }]);
        await WaitAsync(() => manager.Snapshot().Single().IsTerminal);
        var record = manager.Snapshot().Single();
        Assert(record.Status == DownloadStatus.Completed, record.Error);
        var types = await StreamTypesAsync(record.OutputPath);
        Assert(types.Count == 1 && types[0] == (mode == DownloadMode.Audio ? "audio" : "video"), "Legacy muxed extraction returned wrong tracks");
    });
await TestAsync("失败地址与截断流切换备用", async () =>
{
    var fixture = new MediaHandler(video, audio) { TruncatedPrimary = true };
    var storage = NewStorage();
    using var network = new Network(fixture);
    var api = new BilibiliApi(network, () => "");
    await using var manager = new DownloadManager(api, network, new AuthService(network, storage), storage, Config(storage), _ => { });
    manager.Add([Spec(storage, DownloadMode.Video)]);
    await WaitAsync(() => manager.Snapshot().Single().IsTerminal);
    var record = manager.Snapshot().Single();
    Assert(record.Status == DownloadStatus.Completed, record.Error);
    Assert(fixture.Paths.Contains("/backup"), "Backup URL was not used");
});
await TestAsync("画质不符不会自动降档", async () =>
{
    var fixture = new MediaHandler(video, audio);
    using var network = new Network(fixture);
    var api = new BilibiliApi(network, () => "");
    await ThrowsAsync(() => api.StreamsAsync(new("Test", "BV17F411T7Ao", 1, false), new(120, "4K"), DownloadMode.Video, CancellationToken.None));
    Assert(!fixture.Paths.Contains("/video"), "Wrong quality was downloaded");
});
await TestAsync("取消任务后可重试并保留模式", async () =>
{
    var fixture = new MediaHandler(video, audio) { Stall = true };
    var storage = NewStorage();
    using var network = new Network(fixture);
    await using var manager = new DownloadManager(new(network, () => ""), network, new(network, storage), storage, Config(storage), _ => { });
    var id = manager.Add([Spec(storage, DownloadMode.Audio)])[0];
    await WaitAsync(() => manager.Snapshot().Single().Status == DownloadStatus.Downloading);
    manager.Cancel(id);
    await WaitAsync(() => manager.Snapshot().Single().Status == DownloadStatus.Canceled);
    fixture.Stall = false;
    await WaitAsync(() => manager.Retry([id]) == 1);
    await WaitAsync(() => manager.Snapshot().Any(r => r.Status == DownloadStatus.Completed));
    Assert(manager.Snapshot().All(r => r.DownloadMode == DownloadMode.Audio), "Retry lost download mode");
});
await TestAsync("同名文件并发发布不覆盖", async () =>
{
    var directory = Path.Combine(output, Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(directory);
    var target = Path.Combine(directory, "same.mp4");
    await File.WriteAllTextAsync(target, "existing-file");
    var outputs = await Task.WhenAll(FFmpeg.RemuxAsync(remux, videoPath, null, target, DownloadMode.Video, CancellationToken.None),
        FFmpeg.RemuxAsync(remux, videoPath, null, target, DownloadMode.Video, CancellationToken.None));
    Assert(outputs.Distinct(StringComparer.OrdinalIgnoreCase).Count() == 2 && await File.ReadAllTextAsync(target) == "existing-file", "Output collision overwrote a file");
});
await TestAsync("退出恢复与原子记录保存", async () =>
{
    var fixture = new MediaHandler(video, audio) { Stall = true };
    var storage = NewStorage();
    using var network = new Network(fixture);
    var manager = new DownloadManager(new(network, () => ""), network, new(network, storage), storage, Config(storage), _ => { });
    manager.Add([Spec(storage, DownloadMode.Video), Spec(storage, DownloadMode.Audio)]);
    await WaitAsync(() => manager.Snapshot().Any(r => r.Status == DownloadStatus.Downloading));
    await manager.DisposeAsync();
    var recovered = storage.LoadTasks();
    Assert(recovered.Count == 2 && recovered.All(r => r.Status == DownloadStatus.Canceled), "Shutdown left inconsistent records");
    Assert(!Directory.GetFiles(storage.DirectoryPath, "*.tmp", SearchOption.AllDirectories).Any(), "Temporary write file left behind");
});
await TestAsync("命名模板与 Windows 文件名", () =>
{
    Assert(Naming.SafeFilename("CON.txt") == "_CON.txt", "Reserved filename");
    Assert(!Naming.SafeFilename("../../evil:track?.mp4").Contains('/'), "Path traversal in filename");
    Assert(Naming.SafeFilename(new string('中', 250)).Length == 180, "Unbounded filename");
    Assert(Naming.SafeFilename(new string('.', 200) + "name") == "untitled", "Truncation produced an empty filename");
    return Task.CompletedTask;
});
await TestAsync("损坏记录的空任务 id 不影响其余历史", () =>
{
    var storage = NewStorage();
    var valid = new DownloadRecord { TaskId = "retained", Status = DownloadStatus.Completed };
    File.WriteAllText(Path.Combine(storage.DirectoryPath, "tasks.json"), "[{\"task_id\":null}," + JsonSerializer.Serialize(valid, Storage.Json) + "]");
    var records = storage.LoadTasks();
    Assert(records.Count == 1 && records[0].TaskId == valid.TaskId, "A damaged row hid valid history");
    return Task.CompletedTask;
});
await TestAsync("全局限速与取消", async () =>
{
    var limiter = new RateLimiter();
    limiter.Update(32768);
    var watch = Stopwatch.StartNew();
    await limiter.AcquireAsync(8192, CancellationToken.None);
    Assert(watch.ElapsedMilliseconds >= 200, "Rate limit did not apply");
    using var cancel = new CancellationTokenSource(20);
    await ThrowsAsync(() => limiter.AcquireAsync(100000, cancel.Token));
});
await File.WriteAllTextAsync(Path.Combine(output, "report.json"), JsonSerializer.Serialize(new { passed = failures == 0, failures, cases }, Storage.Json));
Console.WriteLine($"{cases.Count - failures}/{cases.Count} passed. {output}");
Environment.ExitCode = failures == 0 ? 0 : 1;

async Task TestAsync(string name, Func<Task> test)
{
    var watch = Stopwatch.StartNew();
    try { await test(); cases.Add(new { name, passed = true, elapsed_ms = watch.Elapsed.TotalMilliseconds }); Console.WriteLine("PASS " + name); }
    catch (Exception ex) { failures++; cases.Add(new { name, passed = false, elapsed_ms = watch.Elapsed.TotalMilliseconds, error = ex.ToString() }); Console.WriteLine("FAIL " + name + " " + ex.Message); }
}
Storage NewStorage() => new(Path.Combine(output, "profiles", Guid.NewGuid().ToString("N")));
AppConfig Config(Storage storage) => new() { SavePath = Path.Combine(storage.DirectoryPath, "downloads"), FfmpegPath = remux, MaxConcurrentTasks = 2 };
TaskSpec Spec(Storage storage, DownloadMode mode) => new("https://www.bilibili.com/video/BV17F411T7Ao", new("Test", "BV17F411T7Ao", 1, false),
    new(mode == DownloadMode.Audio ? 30280 : 80, "source"), Config(storage).SavePath, "test", mode);
async Task<List<string>> StreamTypesAsync(string file)
{
    using var json = JsonDocument.Parse(await ProcessAsync(probe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", file));
    return json.RootElement.GetProperty("streams").EnumerateArray().Select(s => s.GetProperty("codec_type").GetString()!).ToList();
}
async Task<Dictionary<string, List<string>>> PacketHashesAsync(string file)
{
    using var json = JsonDocument.Parse(await ProcessAsync(probe, "-v", "error", "-show_packets", "-show_data_hash", "sha256", "-show_entries", "packet=codec_type,data_hash", "-of", "json", file));
    return json.RootElement.GetProperty("packets").EnumerateArray().GroupBy(packet => packet.GetProperty("codec_type").GetString()!)
        .ToDictionary(group => group.Key, group => group.Select(packet => packet.GetProperty("data_hash").GetString()!).ToList());
}
static void Assert(bool condition, string message) { if (!condition) throw new Exception(message); }
static async Task ThrowsAsync(Func<Task> action)
{
    try { await action(); } catch (Exception) { return; }
    throw new Exception("Expected operation to fail");
}
static async Task WaitAsync(Func<bool> condition)
{
    for (var i = 0; i < 500; i++) { if (condition()) return; await Task.Delay(20); }
    throw new TimeoutException("State transition timed out");
}
static HttpResponseMessage Response(HttpRequestMessage request, object body) => new(HttpStatusCode.OK) { RequestMessage = request, Content = new StringContent(JsonSerializer.Serialize(body)) };
static HttpResponseMessage Redirect(HttpRequestMessage request, string location, HttpStatusCode status)
{
    var response = new HttpResponseMessage(status) { RequestMessage = request };
    response.Headers.Location = new Uri(location);
    return response;
}
static async Task<string> ProcessAsync(string executable, params string[] arguments)
{
    var info = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
    foreach (var argument in arguments) info.ArgumentList.Add(argument);
    using var process = Process.Start(info)!;
    var output = process.StandardOutput.ReadToEndAsync();
    var error = process.StandardError.ReadToEndAsync();
    using var timeout = new CancellationTokenSource(20000);
    await process.WaitForExitAsync(timeout.Token);
    if (process.ExitCode != 0) throw new IOException(await error);
    return await output;
}
sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> handler) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => handler(request, cancellationToken);
}
sealed class MediaHandler(byte[] video, byte[] audio) : HttpMessageHandler
{
    public byte[]? Muxed { get; init; }
    public bool TruncatedPrimary { get; init; }
    public bool Stall { get; set; }
    public System.Collections.Concurrent.ConcurrentBag<string> Paths { get; } = [];
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var path = request.RequestUri!.AbsolutePath;
        Paths.Add(path);
        HttpContent content;
        if (path.Contains("playurl") && Muxed is not null) content = new StringContent(JsonSerializer.Serialize(new { code = 0, data = new { quality = 80, durl = new[] { new { url = "https://media.invalid/muxed", size = Muxed.Length } } } }));
        else if (path.Contains("playurl")) content = new StringContent(JsonSerializer.Serialize(new { code = 0, data = new { dash = new
        {
            video = new[] { new { id = 80, baseUrl = "https://media.invalid/video", backupUrl = new[] { "https://media.invalid/backup" } } },
            audio = new[] { new { id = 30280, baseUrl = "https://media.invalid/audio", bandwidth = 128000 } }
        } } }));
        else if (Stall) content = new BlockingContent();
        else
        {
            var bytes = path == "/muxed" ? Muxed! : path == "/audio" ? audio : video;
            content = new ByteArrayContent(TruncatedPrimary && path == "/video" ? bytes[..(bytes.Length / 2)] : bytes);
            content.Headers.ContentLength = bytes.Length;
        }
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { RequestMessage = request, Content = content });
    }
}
sealed class BlockingContent : HttpContent
{
    protected override bool TryComputeLength(out long length) { length = 1048576; return true; }
    protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) => throw new NotSupportedException();
    protected override Task<Stream> CreateContentReadStreamAsync() => Task.FromResult<Stream>(new BlockingStream());
}
sealed class BlockingStream : Stream
{
    public override bool CanRead => true;
    public override bool CanWrite => false;
    public override bool CanSeek => false;
    public override long Length => 1048576;
    public override long Position { get => 0; set => throw new NotSupportedException(); }
    public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) { await Task.Delay(Timeout.Infinite, cancellationToken); return 0; }
    public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    public override void Flush() { }
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
}
