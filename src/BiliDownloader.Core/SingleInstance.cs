using System.Net;
using System.Net.Sockets;
using System.Text;

namespace BiliDownloader.Core;

// Shares the old client's activation protocol, preventing concurrent writes across versions.
public sealed class SingleInstance : IDisposable
{
    private TcpListener? listener;
    private readonly CancellationTokenSource stopping = new();
    public event Action? Activated;
    public bool Acquire()
    {
        listener = new TcpListener(IPAddress.Loopback, 48379);
        listener.Server.ExclusiveAddressUse = true;
        try { listener.Start(); _ = ListenAsync(); return true; }
        catch (SocketException) { listener.Stop(); listener = null; return false; }
    }
    public static async Task<bool> ActivateExistingAsync()
    {
        using var client = new TcpClient();
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
        try
        {
            await client.ConnectAsync(IPAddress.Loopback, 48379, timeout.Token);
            await client.GetStream().WriteAsync(Encoding.ASCII.GetBytes("BiliDownloader.SHOW\n"), timeout.Token);
            var data = new byte[16];
            var count = await client.GetStream().ReadAsync(data, timeout.Token);
            return Encoding.ASCII.GetString(data, 0, count).StartsWith("OK");
        }
        catch (Exception ex) when (ex is IOException or SocketException or OperationCanceledException) { return false; }
    }
    private async Task ListenAsync()
    {
        try
        {
            while (!stopping.IsCancellationRequested)
            {
                using var client = await listener!.AcceptTcpClientAsync(stopping.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(stopping.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(1));
                try
                {
                    var buffer = new byte[64];
                    var count = await client.GetStream().ReadAsync(buffer, timeout.Token);
                    if (Encoding.ASCII.GetString(buffer, 0, count) == "BiliDownloader.SHOW\n")
                    { Activated?.Invoke(); await client.GetStream().WriteAsync("OK\n"u8.ToArray(), timeout.Token); }
                }
                catch (Exception ex) when (ex is IOException or OperationCanceledException) { }
            }
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException or ObjectDisposedException) { }
    }
    public void Dispose() { stopping.Cancel(); listener?.Stop(); stopping.Dispose(); }
}
