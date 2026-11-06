using System.Diagnostics;

namespace BiliDownloader.Core;

public sealed class RateLimiter
{
    private readonly object gate = new();
    private double nextTime;
    private double limit;
    public void Update(double bytesPerSecond)
    {
        lock (gate) { limit = Math.Max(0, bytesPerSecond); nextTime = Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency; }
    }
    public async Task AcquireAsync(int bytes, CancellationToken ct)
    {
        var remaining = bytes;
        while (remaining > 0)
        {
            double delay;
            int amount;
            lock (gate)
            {
                if (limit <= 0) return;
                // Short reservations keep cancellation and live limit changes responsive.
                amount = Math.Min(remaining, Math.Max(1, (int)Math.Min(int.MaxValue, limit / 10)));
                var now = Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;
                nextTime = Math.Max(now, nextTime) + amount / limit;
                delay = nextTime - now;
            }
            await Task.Delay(TimeSpan.FromSeconds(delay), ct);
            remaining -= amount;
        }
    }
}
