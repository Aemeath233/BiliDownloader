using System.Collections.Concurrent;
using System.Diagnostics;
using System.Threading.Channels;

namespace BiliDownloader.Core;

public sealed class DownloadManager : IAsyncDisposable
{
    private readonly object gate = new();
    private readonly Dictionary<string, DownloadRecord> records;
    private readonly Dictionary<string, (CancellationTokenSource Cancel, Task Worker)> active = new();
    private readonly Queue<string> queue = new();
    private readonly CancellationTokenSource stopping = new();
    private readonly RateLimiter limiter = new();
    private readonly Channel<List<DownloadRecord>> writes = Channel.CreateBounded<List<DownloadRecord>>(new BoundedChannelOptions(1) { FullMode = BoundedChannelFullMode.DropOldest });
    private readonly BilibiliApi api;
    private readonly Network network;
    private readonly AuthService auth;
    private readonly Storage storage;
    private readonly Action<string> log;
    private AppConfig config;
    private readonly Task scheduler;
    private readonly Task writer;
    public string PersistenceError { get; private set; } = "";

    public DownloadManager(BilibiliApi api, Network network, AuthService auth, Storage storage, AppConfig config, Action<string> log)
    {
        this.api = api; this.network = network; this.auth = auth; this.storage = storage; this.config = config; this.log = log;
        records = storage.LoadTasks().ToDictionary(r => r.TaskId);
        limiter.Update(config.SpeedLimitMb * 1048576);
        writer = Task.Run(WriteLoopAsync);
        scheduler = Task.Run(ScheduleAsync);
    }
    public void UpdateConfig(AppConfig value)
    {
        lock (gate) config = value;
        limiter.Update(value.SpeedLimitMb * 1048576);
    }
    public List<DownloadRecord> Snapshot() { lock (gate) return records.Values.Select(r => r.Snapshot()).ToList(); }
    public (int Active, int Queued, int Completed, double SpeedBps) Metrics()
    {
        lock (gate)
        {
            var active = 0;
            var queued = 0;
            var completed = 0;
            var speed = 0.0;
            foreach (var record in records.Values)
            {
                if (record.Status == DownloadStatus.Queued) queued++;
                else if (!record.IsTerminal) active++;
                if (record.Status == DownloadStatus.Completed) completed++;
                if (record.Status == DownloadStatus.Downloading) speed += record.SpeedBps;
            }
            return (active, queued, completed, speed);
        }
    }
    public int CountDuplicates(IEnumerable<Episode> episodes, Quality quality, DownloadMode mode)
    {
        var identities = episodes.Select(e => (e.Bvid, e.Cid, e.IsBangumi)).ToHashSet();
        lock (gate) return records.Values.Count(r => !r.CanRetry && r.Quality.Qn == quality.Qn && r.DownloadMode == mode && identities.Contains((r.Episode.Bvid, r.Episode.Cid, r.Episode.IsBangumi)));
    }
    public List<string> Add(IEnumerable<TaskSpec> specifications)
    {
        var specs = specifications.ToList();
        foreach (var spec in specs) Directory.CreateDirectory(spec.SaveDir);
        var added = new List<string>();
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(stopping.IsCancellationRequested, this);
            var reserved = records.Values.Where(r => !r.IsTerminal || File.Exists(r.OutputPath)).Select(r => Path.GetFullPath(r.OutputPath)).ToHashSet(StringComparer.OrdinalIgnoreCase);
            var prepared = specs.Select(spec =>
            {
                var path = Naming.AvailablePath(Path.Combine(spec.SaveDir, Naming.SafeFilename(spec.FilenameStem) + Display.Extension(spec.DownloadMode)), reserved);
                reserved.Add(path);
                return new DownloadRecord { SourceUrl = spec.SourceUrl, Episode = spec.Episode, Quality = spec.Quality,
                    SaveDir = Path.GetFullPath(spec.SaveDir), OutputPath = path, Title = spec.Episode.Title, DownloadMode = spec.DownloadMode };
            }).ToList();
            foreach (var record in prepared) { records.Add(record.TaskId, record); queue.Enqueue(record.TaskId); added.Add(record.TaskId); }
            PersistLocked();
        }
        if (added.Count > 0) log($"已添加 {added.Count} 个下载任务");
        return added;
    }
    public void Cancel(string id)
    {
        lock (gate)
        {
            if (!records.TryGetValue(id, out var record) || record.IsTerminal) return;
            if (active.TryGetValue(id, out var work)) { record.Message = "正在取消"; work.Cancel.Cancel(); }
            else { record.Status = DownloadStatus.Canceled; record.Message = "已取消"; record.FinishedAt = DownloadRecord.Now(); }
            PersistLocked();
        }
    }
    public int Remove(IEnumerable<string> ids)
    {
        var count = 0;
        lock (gate)
        {
            foreach (var id in ids.Distinct())
                if (!active.ContainsKey(id) && records.TryGetValue(id, out var record) && (record.IsTerminal || record.Status == DownloadStatus.Queued) && records.Remove(id)) count++;
            if (count > 0) PersistLocked();
        }
        return count;
    }
    public int Retry(IEnumerable<string> ids)
    {
        List<TaskSpec> specs;
        lock (gate) specs = ids.Distinct().Where(id => !active.ContainsKey(id) && records.TryGetValue(id, out var r) && r.CanRetry)
            .Select(id => records[id]).Select(r => new TaskSpec(r.SourceUrl, r.Episode, r.Quality, r.SaveDir, Path.GetFileNameWithoutExtension(r.OutputPath), r.DownloadMode)).ToList();
        return Add(specs).Count;
    }
    private async Task ScheduleAsync()
    {
        try
        {
            using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(150));
            while (await timer.WaitForNextTickAsync(stopping.Token))
                lock (gate)
                {
                    var limit = config.MaxConcurrentTasks;
                    while (active.Count < limit && queue.TryDequeue(out var id))
                    {
                        if (!records.TryGetValue(id, out var record) || record.Status != DownloadStatus.Queued) continue;
                        var cancel = CancellationTokenSource.CreateLinkedTokenSource(stopping.Token);
                        record.Status = DownloadStatus.Preparing;
                        active.Add(id, (cancel, Task.Run(() => RunAsync(id, cancel.Token))));
                    }
                }
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested) { }
    }
    private async Task RunAsync(string id, CancellationToken ct)
    {
        DownloadRecord record;
        AppConfig options;
        lock (gate) { record = records[id]; options = config; }
        var temporary = Path.Combine(record.SaveDir, ".bili_client", record.TaskId);
        try
        {
            Directory.CreateDirectory(temporary);
            Change(id, DownloadStatus.Preparing, "读取媒体流");
            try { await auth.EnsureFreshAsync(ct); }
            catch (Exception ex) when (ex is not OperationCanceledException) { log("登录态检查 " + Network.SafeError(ex)); }
            var streams = await api.StreamsAsync(record.Episode, record.Quality, record.DownloadMode, ct);
            ct.ThrowIfCancellationRequested();
            Change(id, DownloadStatus.Downloading, "下载中");
            using var streamCancellation = CancellationTokenSource.CreateLinkedTokenSource(ct);
            async Task DownloadOne(MediaStream? stream, string name)
            {
                if (stream is null) return;
                try { await DownloadStreamAsync(id, stream, Path.Combine(temporary, name), streamCancellation.Token); }
                catch { streamCancellation.Cancel(); throw; }
            }
            await Task.WhenAll(DownloadOne(streams.Video, "video.m4s"), DownloadOne(streams.Audio, "audio.m4s"));
            ct.ThrowIfCancellationRequested();
            Change(id, DownloadStatus.Merging, record.DownloadMode == DownloadMode.Combined ? "合并音视频" : "保存媒体文件");
            var input = Path.Combine(temporary, streams.Video is null ? "audio.m4s" : "video.m4s");
            var audio = streams.Video is not null && streams.Audio is not null ? Path.Combine(temporary, "audio.m4s") : null;
            var published = await FFmpeg.RemuxAsync(options.FfmpegPath, input, audio, record.OutputPath, record.DownloadMode, ct);
            lock (gate)
            {
                // A successful publication remains completed even if cancellation arrives just afterwards.
                record.OutputPath = published; record.Status = DownloadStatus.Completed; record.Message = "下载完成";
                record.Progress.Percent = 100; record.SpeedBps = 0; record.FinishedAt = DownloadRecord.Now(); PersistLocked();
            }
            log("下载完成 " + record.Title);
        }
        catch (Exception error)
        {
            lock (gate)
            {
                record.Status = ct.IsCancellationRequested ? DownloadStatus.Canceled : DownloadStatus.Failed;
                record.Message = ct.IsCancellationRequested ? "已取消" : Network.SafeError(error);
                record.Error = record.Status == DownloadStatus.Failed ? record.Message : "";
                record.SpeedBps = 0; record.FinishedAt = DownloadRecord.Now(); PersistLocked();
            }
            log($"{Display.Status(record.Status)} {record.Title} {record.Error}");
        }
        finally
        {
            if (options.CleanupTempFiles)
            {
                try { Directory.Delete(temporary, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            }
            lock (gate)
            {
                if (active.Remove(id, out var work)) work.Cancel.Dispose();
                speedSamples.Remove(id);
            }
        }
    }
    private async Task DownloadStreamAsync(string id, MediaStream stream, string destination, CancellationToken ct)
    {
        Exception? last = null;
        foreach (var url in stream.Urls)
        {
            ct.ThrowIfCancellationRequested();
            try
            {
                using var response = await network.SendAsync(url, referer: stream.Referer, cancellationToken: ct);
                var total = response.Content.Headers.ContentLength ?? stream.Size;
                SetProgress(id, stream.Kind, 0, total);
                await using var source = await response.Content.ReadAsStreamAsync(ct);
                await using var file = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None, 131072, true);
                var buffer = new byte[131072];
                long written = 0;
                while (true)
                {
                    using var idle = CancellationTokenSource.CreateLinkedTokenSource(ct);
                    idle.CancelAfter(TimeSpan.FromSeconds(30));
                    var count = await source.ReadAsync(buffer, idle.Token);
                    if (count == 0) break;
                    await limiter.AcquireAsync(count, ct);
                    await file.WriteAsync(buffer.AsMemory(0, count), ct);
                    written += count;
                    SetProgress(id, stream.Kind, written, total);
                }
                if (written == 0 || (total > 0 && written != total)) throw new IOException("媒体流不完整，请重试");
                return;
            }
            catch (Exception ex) when (!ct.IsCancellationRequested && ex is HttpRequestException or IOException or OperationCanceledException)
            {
                last = ex;
                log("媒体流尝试备用地址 " + Network.SafeError(ex));
            }
        }
        throw new IOException("媒体流下载失败 " + (last is null ? "没有可用地址" : Network.SafeError(last)));
    }
    private readonly Dictionary<string, (long Time, long Bytes)> speedSamples = new();
    private void SetProgress(string id, string kind, long written, long total)
    {
        lock (gate)
        {
            var record = records[id];
            var progress = record.Progress;
            if (kind == "video") { progress.VideoDownloadedBytes = written; progress.VideoTotalBytes = total; }
            else { progress.AudioDownloadedBytes = written; progress.AudioTotalBytes = total; }
            progress.TotalBytes = progress.VideoTotalBytes + progress.AudioTotalBytes;
            progress.DownloadedBytes = progress.VideoDownloadedBytes + progress.AudioDownloadedBytes;
            progress.Percent = progress.TotalBytes > 0 ? Math.Min(100, progress.DownloadedBytes * 100.0 / progress.TotalBytes) : 0;
            var now = Stopwatch.GetTimestamp();
            if (speedSamples.TryGetValue(id, out var sample))
            {
                var elapsed = (now - sample.Time) / (double)Stopwatch.Frequency;
                if (elapsed >= 0.5) { record.SpeedBps = Math.Max(0, (progress.DownloadedBytes - sample.Bytes) / elapsed); speedSamples[id] = (now, progress.DownloadedBytes); }
            }
            else speedSamples[id] = (now, progress.DownloadedBytes);
        }
    }
    private void Change(string id, DownloadStatus status, string message)
    {
        lock (gate)
        {
            var record = records[id]; record.Status = status; record.Message = message;
            if (status == DownloadStatus.Downloading) record.StartedAt ??= DownloadRecord.Now();
            else record.SpeedBps = 0;
            PersistLocked();
        }
    }
    private void PersistLocked() => writes.Writer.TryWrite(records.Values.Select(r => r.Snapshot()).ToList());
    private async Task WriteLoopAsync()
    {
        await foreach (var snapshot in writes.Reader.ReadAllAsync())
        {
            try { storage.SaveTasks(snapshot); PersistenceError = ""; }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            { PersistenceError = "下载记录保存失败 " + Network.SafeError(ex); log(PersistenceError); }
        }
    }
    public async ValueTask DisposeAsync()
    {
        await stopping.CancelAsync();
        await scheduler;
        Task[] workers;
        lock (gate)
        {
            while (queue.TryDequeue(out var id))
                if (records.TryGetValue(id, out var record) && record.Status == DownloadStatus.Queued)
                { record.Status = DownloadStatus.Canceled; record.Message = "程序退出，可重试"; record.FinishedAt = DownloadRecord.Now(); }
            workers = active.Values.Select(w => w.Worker).ToArray();
        }
        await Task.WhenAll(workers);
        lock (gate) PersistLocked();
        writes.Writer.TryComplete();
        await writer;
        stopping.Dispose();
    }
}
