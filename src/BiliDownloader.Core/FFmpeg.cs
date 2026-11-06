using System.Diagnostics;

namespace BiliDownloader.Core;

public static class FFmpeg
{
    public static string Resolve(string command)
    {
        if (Path.IsPathFullyQualified(command)) return File.Exists(command) ? command : throw new FileNotFoundException("指定的 FFmpeg 不存在");
        var bundled = Path.Combine(AppContext.BaseDirectory, "ffmpeg.exe");
        if (File.Exists(bundled)) return bundled;
        foreach (var directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            if (!Path.IsPathFullyQualified(directory)) continue;
            var file = Path.Combine(directory, "ffmpeg.exe");
            if (File.Exists(file)) return file;
        }
        throw new FileNotFoundException("未找到 FFmpeg，请在设置中选择 ffmpeg.exe");
    }
    public static async Task<string> RemuxAsync(string command, string input, string? audio, string output, DownloadMode mode, CancellationToken ct)
    {
        var temporary = Path.Combine(Path.GetDirectoryName(output)!, "." + Guid.NewGuid().ToString("N") + ".tmp.mp4");
        var info = new ProcessStartInfo(Resolve(command)) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardError = true, RedirectStandardOutput = true };
        foreach (var arg in new[] { "-hide_banner", "-nostdin", "-y", "-i", input }) info.ArgumentList.Add(arg);
        if (audio is not null) { info.ArgumentList.Add("-i"); info.ArgumentList.Add(audio); }
        var arguments = mode switch
        {
            DownloadMode.Audio => new[] { "-map", "0:a:0", "-vn", "-c", "copy" },
            DownloadMode.Video => ["-map", "0:v:0", "-an", "-c", "copy"],
            _ when audio is not null => ["-map", "0:v:0", "-map", "1:a:0", "-c", "copy"],
            _ => ["-map", "0:v:0", "-map", "0:a?", "-c", "copy"]
        };
        foreach (var arg in arguments.Concat(new[] { "-movflags", "+faststart", "-f", "mp4", temporary })) info.ArgumentList.Add(arg);
        try
        {
            using var process = Process.Start(info) ?? throw new IOException("无法启动 FFmpeg");
            using var registration = ct.Register(() => { try { if (!process.HasExited) process.Kill(true); } catch (InvalidOperationException) { } });
            var errors = process.StandardError.ReadToEndAsync();
            var stdout = process.StandardOutput.ReadToEndAsync();
            await process.WaitForExitAsync(CancellationToken.None);
            var message = await errors;
            await stdout;
            ct.ThrowIfCancellationRequested();
            if (process.ExitCode != 0 || !File.Exists(temporary) || new FileInfo(temporary).Length == 0)
                throw new IOException("音视频封装失败。" + (message.Length > 800 ? message[^800..] : message));
            // File.Move without overwrite is the publication boundary. Never replace an existing download.
            for (var attempt = 0; ; attempt++)
            {
                ct.ThrowIfCancellationRequested();
                var candidate = Naming.AvailablePath(output);
                try { File.Move(temporary, candidate); return candidate; }
                catch (IOException) when (attempt < 100 && File.Exists(candidate)) { }
            }
        }
        finally { File.Delete(temporary); }
    }
}
