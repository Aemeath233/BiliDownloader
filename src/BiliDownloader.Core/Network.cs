using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace BiliDownloader.Core;

public sealed class Network : IDisposable
{
    private readonly HttpClient client;
    private static readonly HashSet<string> ShareHosts = ["bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv", "www.b23.tv"];
    public Network(HttpMessageHandler? handler = null)
    {
        client = new HttpClient(handler ?? new SocketsHttpHandler
        {
            AllowAutoRedirect = false, UseCookies = false, ConnectTimeout = TimeSpan.FromSeconds(15),
            PooledConnectionLifetime = TimeSpan.FromMinutes(5)
        }) { Timeout = Timeout.InfiniteTimeSpan };
    }
    public static bool AccountHost(Uri uri) => uri.Host.Equals("bilibili.com", StringComparison.OrdinalIgnoreCase) || uri.Host.EndsWith(".bilibili.com", StringComparison.OrdinalIgnoreCase);
    public static Uri ShareUri(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || !ShareHosts.Contains(uri.Host.ToLowerInvariant()) ||
            uri.Scheme is not ("https" or "http") || uri.UserInfo.Length != 0 || !uri.IsDefaultPort)
            throw new InvalidDataException("只支持 bilibili.com 和 b23.tv 的视频链接");
        return new UriBuilder(uri) { Scheme = "https", Port = -1 }.Uri;
    }
    public static Uri MediaUri(string value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) || uri.Scheme is not ("https" or "http") || uri.UserInfo.Length != 0)
            throw new InvalidDataException("媒体地址无效");
        return uri.Scheme == "https" ? uri : new UriBuilder(uri) { Scheme = "https", Port = uri.IsDefaultPort ? -1 : uri.Port }.Uri;
    }
    public async Task<HttpResponseMessage> SendAsync(Uri uri, string cookies = "", string referer = "https://www.bilibili.com/",
        CancellationToken cancellationToken = default, bool shareOnly = false, HttpMethod? method = null,
        Dictionary<string, string>? form = null)
    {
        for (var redirect = 0; redirect < 8; redirect++)
        {
            if (shareOnly) uri = ShareUri(uri.AbsoluteUri);
            if (uri.Scheme != "https" || uri.UserInfo.Length != 0) throw new InvalidDataException("请求必须使用 HTTPS，地址不能包含登录信息");
            for (var attempt = 0; ; attempt++)
            {
                using var request = new HttpRequestMessage(method ?? HttpMethod.Get, uri);
                request.Headers.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36");
                request.Headers.Referrer = new Uri(referer);
                if (AccountHost(uri) && !string.IsNullOrEmpty(cookies)) request.Headers.TryAddWithoutValidation("Cookie", cookies);
                if (form is not null) request.Content = new FormUrlEncodedContent(form);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                timeout.CancelAfter(TimeSpan.FromSeconds(30));
                var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, timeout.Token);
                var status = (int)response.StatusCode;
                if (status is 301 or 302 or 303 or 307 or 308)
                {
                    var location = response.Headers.Location;
                    response.Dispose();
                    if (location is null) throw new HttpRequestException("服务器未提供跳转地址");
                    var redirected = new Uri(uri, location);
                    if (redirected.Scheme != "https" || redirected.UserInfo.Length != 0) throw new InvalidDataException("已阻止不安全的跳转地址");
                    if (form is not null && (redirected.Host != uri.Host || redirected.Port != uri.Port))
                        throw new InvalidDataException("已阻止携带登录信息的跨站跳转");
                    uri = redirected;
                    if (status == 303) { method = HttpMethod.Get; form = null; }
                    break;
                }
                if (attempt < 2 && status is 412 or 429 or 500 or 502 or 503 or 504 && form is null)
                {
                    response.Dispose();
                    await Task.Delay(TimeSpan.FromSeconds(attempt + 1), cancellationToken);
                    continue;
                }
                if (!response.IsSuccessStatusCode)
                {
                    response.Dispose();
                    throw new HttpRequestException($"请求失败，HTTP {status}");
                }
                return response;
            }
        }
        throw new HttpRequestException("链接跳转次数过多");
    }
    public async Task<JsonElement> JsonAsync(string url, string cookies = "", string referer = "https://www.bilibili.com/", CancellationToken cancellationToken = default)
    {
        using var response = await SendAsync(new Uri(url), cookies, referer, cancellationToken);
        return await ReadJsonAsync(response, cancellationToken);
    }
    public static async Task<JsonElement> ReadJsonAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        var body = await response.Content.ReadAsStringAsync(timeout.Token);
        try { using var json = JsonDocument.Parse(body); return json.RootElement.Clone(); }
        catch (JsonException) { throw new InvalidDataException("接口返回异常，请稍后重试"); }
    }
    public static void Check(JsonElement root)
    {
        var status = root.Get("code");
        if (status.ValueKind != JsonValueKind.Number || !status.TryGetInt64(out var code)) throw new InvalidDataException("接口返回缺少有效的整数状态码");
        if (code != 0) throw new InvalidDataException(root.Text("message", root.Text("msg", "接口请求失败")));
    }
    public static string SafeError(Exception error)
    {
        if (error is OperationCanceledException) return "操作已取消或网络响应超时";
        var message = Regex.Replace(error.Message, @"https?://\S+", "<网络地址>");
        message = Regex.Replace(message, @"(?i)(SESSDATA|bili_jct|refresh_token|qrcode_key|csrf|token|sign)[""']?\s*[=:]\s*[""']?[^\s;,&""'<>]+", "$1=<已隐藏>");
        return message.Length > 500 ? message[..500] : message;
    }
    public void Dispose() => client.Dispose();
}

internal static class JsonFields
{
    public static JsonElement Get(this JsonElement element, string key) => element.ValueKind == JsonValueKind.Object && element.TryGetProperty(key, out var value) ? value : default;
    public static string Text(this JsonElement element, string key, string fallback = "")
    {
        var value = element.Get(key);
        return value.ValueKind is JsonValueKind.String ? value.GetString() ?? fallback : value.ValueKind == JsonValueKind.Number ? value.ToString() : fallback;
    }
    public static long Long(this JsonElement element, string key, long fallback = 0) => long.TryParse(element.Text(key), out var value) ? value : fallback;
    public static int Int(this JsonElement element, string key, int fallback = 0) => (int)Math.Clamp(element.Long(key, fallback), int.MinValue, int.MaxValue);
    public static bool Bool(this JsonElement element, string key) => element.Get(key).ValueKind == JsonValueKind.True || element.Int(key) != 0;
    public static IEnumerable<JsonElement> Items(this JsonElement element) => element.ValueKind == JsonValueKind.Array ? element.EnumerateArray() : [];
}
