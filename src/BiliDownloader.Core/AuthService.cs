using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace BiliDownloader.Core;

public sealed record AuthSession
{
    public Dictionary<string, string> Cookies { get; init; } = new();
    public string RefreshToken { get; init; } = "";
    public double UpdatedAt { get; init; } = DownloadRecord.Now();
    public double LastCheckedAt { get; init; }
    public string CookieHeader() => string.Join("; ", Cookies.Where(p => p.Value.Length > 0).Select(p => $"{p.Key}={Normalize(p.Value)}"));
    public static string Normalize(string value) => Uri.EscapeDataString(Uri.UnescapeDataString(value.Trim()));
}
public sealed record QrLogin(string Url, string QrcodeKey);
public sealed record QrStatus(int Code, string Message);

public sealed class AuthService(Network network, Storage storage)
{
    private readonly SemaphoreSlim refreshLock = new(1, 1);
    private readonly object sessionLock = new();
    private AuthSession? session;
    public bool IsLoggedIn { get { lock (sessionLock) return session?.Cookies.GetValueOrDefault("SESSDATA", "").Length > 0; } }
    public string Cookies { get { lock (sessionLock) return session?.CookieHeader() ?? ""; } }
    public void Load()
    {
        if (!File.Exists(storage.AuthPath)) return;
        using var root = JsonDocument.Parse(File.ReadAllText(storage.AuthPath));
        var element = root.RootElement;
        if (element.Text("protected") is { Length: > 0 } cipher)
        {
            var plaintext = ProtectedData.Unprotect(Convert.FromBase64String(cipher), null, DataProtectionScope.CurrentUser);
            try { session = JsonSerializer.Deserialize<AuthSession>(plaintext, Storage.Json); }
            finally { CryptographicOperations.ZeroMemory(plaintext); }
        }
        else session = (element.Get("session").ValueKind == JsonValueKind.Object ? element.Get("session") : element).Deserialize<AuthSession>(Storage.Json);
        if (session is not null)
        {
            session = session with { Cookies = (session.Cookies ?? new()).Where(pair => Regex.IsMatch(pair.Key, @"^[A-Za-z0-9_]+$") && !string.IsNullOrEmpty(pair.Value)).ToDictionary() };
            if (element.Get("protected").ValueKind != JsonValueKind.String) Save(session);
        }
    }
    public void SetSessdata(string value)
    {
        lock (sessionLock)
        {
            if (string.IsNullOrWhiteSpace(value)) { File.Delete(storage.AuthPath); session = null; }
            else Save(new AuthSession { Cookies = new() { ["SESSDATA"] = value.Trim() } });
        }
    }
    private void Save(AuthSession next)
    {
        var plain = JsonSerializer.SerializeToUtf8Bytes(next, Storage.Json);
        byte[] encrypted;
        try { encrypted = ProtectedData.Protect(plain, null, DataProtectionScope.CurrentUser); }
        finally { CryptographicOperations.ZeroMemory(plain); }
        storage.Write("auth.json", new { version = 2, protection = "dpapi-current-user", @protected = Convert.ToBase64String(encrypted) });
        session = next;
    }
    public async Task<QrLogin> CreateQrAsync(CancellationToken ct)
    {
        var root = await network.JsonAsync("https://passport.bilibili.com/x/passport-login/web/qrcode/generate", cancellationToken: ct);
        Network.Check(root);
        var qr = new QrLogin(root.Get("data").Text("url"), root.Get("data").Text("qrcode_key"));
        if (qr.Url.Length == 0 || qr.QrcodeKey.Length == 0) throw new InvalidDataException("二维码申请结果不完整");
        return qr;
    }
    public async Task<QrStatus> PollQrAsync(QrLogin qr, CancellationToken ct)
    {
        using var response = await network.SendAsync(new Uri("https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=" + Uri.EscapeDataString(qr.QrcodeKey)), cancellationToken: ct);
        var root = await Network.ReadJsonAsync(response, ct);
        Network.Check(root);
        var data = root.Get("data");
        var code = data.Int("code", -1);
        if (code != 0) return new(code, data.Text("message", "等待扫码"));
        var cookies = ResponseCookies(response);
        var callback = data.Text("url");
        if (Uri.TryCreate(callback, UriKind.Absolute, out var uri))
            foreach (var part in uri.Query.TrimStart('?').Split('&'))
            {
                var pair = part.Split('=', 2);
                if (pair.Length == 2 && pair[0] is "SESSDATA" or "bili_jct" or "DedeUserID" or "DedeUserID__ckMd5" or "sid") cookies[pair[0]] = pair[1];
            }
        if (!cookies.ContainsKey("SESSDATA")) throw new InvalidDataException("未取得登录凭证，请刷新二维码重试");
        lock (sessionLock) Save(new AuthSession { Cookies = cookies, RefreshToken = data.Text("refresh_token") });
        return new(0, "登录成功");
    }
    public async Task EnsureFreshAsync(CancellationToken ct, bool force = false)
    {
        await refreshLock.WaitAsync(ct);
        try
        {
            AuthSession? original;
            lock (sessionLock) original = session;
            if (original is null || !original.Cookies.ContainsKey("SESSDATA") || (!force && DownloadRecord.Now() - original.LastCheckedAt < 600)) return;
            var header = original.CookieHeader();
            var info = await network.JsonAsync("https://passport.bilibili.com/x/passport-login/web/cookie/info", header, cancellationToken: ct);
            if (info.Int("code") == -101) throw new InvalidDataException("登录已失效，请重新扫码");
            Network.Check(info);
            var next = original with { LastCheckedAt = DownloadRecord.Now() };
            if (info.Get("data").Bool("refresh"))
            {
                if (original.RefreshToken.Length == 0) throw new InvalidDataException("登录需要更新，请重新扫码登录");
                using var rsa = RSA.Create();
                rsa.ImportFromPem(PublicKey);
                var timestamp = info.Get("data").Long("timestamp", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                var path = Convert.ToHexStringLower(rsa.Encrypt(Encoding.UTF8.GetBytes($"refresh_{timestamp}"), RSAEncryptionPadding.OaepSHA256));
                using var correspond = await network.SendAsync(new Uri("https://www.bilibili.com/correspond/1/" + path), header, cancellationToken: ct);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
                timeout.CancelAfter(TimeSpan.FromSeconds(30));
                var html = await correspond.Content.ReadAsStringAsync(timeout.Token);
                var match = Regex.Match(html, "<div\\s+id=[\"']1-name[\"']>([^<]+)</div>");
                if (!match.Success) throw new InvalidDataException("登录更新失败，请重新扫码");
                using var refresh = await network.SendAsync(new Uri("https://passport.bilibili.com/x/passport-login/web/cookie/refresh"), header,
                    cancellationToken: ct, method: HttpMethod.Post, form: new()
                    {
                        ["csrf"] = original.Cookies.GetValueOrDefault("bili_jct", ""), ["refresh_csrf"] = match.Groups[1].Value.Trim(),
                        ["source"] = "main_web", ["refresh_token"] = original.RefreshToken
                    });
                var payload = await Network.ReadJsonAsync(refresh, ct);
                Network.Check(payload);
                var cookies = new Dictionary<string, string>(original.Cookies);
                foreach (var pair in ResponseCookies(refresh)) cookies[pair.Key] = pair.Value;
                next = next with { Cookies = cookies, RefreshToken = payload.Get("data").Text("refresh_token", original.RefreshToken), UpdatedAt = DownloadRecord.Now() };
                // Save the replacement before confirming, so a connection failure cannot discard it.
                lock (sessionLock) { if (ReferenceEquals(session, original)) Save(next); else return; }
                using var confirm = await network.SendAsync(new Uri("https://passport.bilibili.com/x/passport-login/web/confirm/refresh"), next.CookieHeader(),
                    cancellationToken: ct, method: HttpMethod.Post, form: new() { ["csrf"] = cookies.GetValueOrDefault("bili_jct", ""), ["refresh_token"] = original.RefreshToken });
            }
            lock (sessionLock) { if (ReferenceEquals(session, original)) Save(next); }
        }
        finally { refreshLock.Release(); }
    }
    private static Dictionary<string, string> ResponseCookies(HttpResponseMessage response)
    {
        var result = new Dictionary<string, string>();
        if (!response.Headers.TryGetValues("Set-Cookie", out var headers)) return result;
        foreach (var header in headers)
        {
            var pair = header.Split(';', 2)[0].Split('=', 2);
            if (pair.Length == 2 && Regex.IsMatch(pair[0], @"^[A-Za-z0-9_]+$")) result[pair[0]] = pair[1];
        }
        return result;
    }
    private const string PublicKey = """
        -----BEGIN PUBLIC KEY-----
        MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
        Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
        nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
        JNrRuoEUXpabUzGB8QIDAQAB
        -----END PUBLIC KEY-----
        """;
}
