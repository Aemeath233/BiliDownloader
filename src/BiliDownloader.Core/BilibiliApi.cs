using System.Text.Json;
using System.Text.RegularExpressions;

namespace BiliDownloader.Core;

public sealed partial class BilibiliApi(Network network, Func<string> getCookies)
{
    public static string ExtractUrl(string text)
    {
        var url = UrlPattern().Match(text);
        if (url.Success) return Network.ShareUri(url.Value.TrimEnd('.', ',', ';', '!', ')', ']', '}')).AbsoluteUri;
        var id = IdPattern().Match(text);
        if (!id.Success) throw new InvalidDataException("粘贴 B 站分享链接，或输入 BV、ep、ss 编号");
        return $"https://www.bilibili.com/{(id.Value.StartsWith("BV") ? "video" : "bangumi/play")}/{id.Value}";
    }
    public async Task<MediaResult> ParseAsync(string text, CancellationToken cancellationToken = default)
    {
        var source = ExtractUrl(text);
        var normalized = source;
        if (new Uri(source).Host.EndsWith("b23.tv", StringComparison.OrdinalIgnoreCase))
        {
            using var response = await network.SendAsync(new Uri(source), getCookies(), cancellationToken: cancellationToken, shareOnly: true);
            normalized = Network.ShareUri(response.RequestMessage?.RequestUri?.AbsoluteUri ?? source).AbsoluteUri;
        }
        var id = IdPattern().Match(new Uri(normalized).AbsolutePath).Value;
        var episodes = new List<Episode>();
        var selected = 0;
        string title, folder, owner;
        if (id.StartsWith("BV"))
        {
            var root = await LoadAsync($"https://api.bilibili.com/x/web-interface/view?bvid={id}", normalized, cancellationToken);
            var data = root.Get("data");
            title = data.Text("title", "Bilibili 视频");
            owner = data.Get("owner").Text("name");
            folder = owner.Length > 0 ? owner : title;
            var pages = data.Get("pages").Items().ToList();
            foreach (var page in pages)
                episodes.Add(new Episode(pages.Count > 1 ? $"{title} - P{page.Int("page")} {page.Text("part")}" : title, id, page.Long("cid"), false));
            var match = Regex.Match(normalized + "&" + text, @"[?&]p=(\d+)");
            if (match.Success && int.TryParse(match.Groups[1].Value, out var number)) selected = Math.Clamp(number - 1, 0, Math.Max(0, pages.Count - 1));
        }
        else if (id.StartsWith("ep") || id.StartsWith("ss"))
        {
            var root = await LoadAsync($"https://api.bilibili.com/pgc/view/web/season?{(id.StartsWith("ep") ? "ep_id" : "season_id")}={id[2..]}", normalized, cancellationToken);
            var data = root.Get("result");
            title = data.Text("season_title", data.Text("title", "Bilibili 番剧"));
            folder = owner = title;
            var items = data.Get("episodes").Items().ToList();
            foreach (var item in items)
            {
                if (id == "ep" + item.Text("ep_id", item.Text("id"))) selected = episodes.Count;
                var label = $"{item.Text("title")} {item.Text("long_title")}".Trim();
                episodes.Add(new Episode(items.Count > 1 ? $"{title} - {label}" : title, item.Text("bvid"), item.Long("cid"), true));
            }
        }
        else throw new InvalidDataException("只支持 BV、ep、ss 视频链接");
        if (episodes.Count == 0 || episodes.Any(e => e.Cid <= 0 || e.Bvid.Length == 0)) throw new InvalidDataException("没有解析到可下载的分集");
        return new MediaResult(source, normalized, title, episodes, selected, Naming.SafeFilename(folder), owner);
    }
    private async Task<JsonElement> LoadAsync(string url, string referer, CancellationToken ct)
    {
        var root = await network.JsonAsync(url, getCookies(), referer, ct);
        Network.Check(root);
        return root;
    }
    private async Task<JsonElement> PlayAsync(Episode episode, int qn, CancellationToken ct)
    {
        var route = episode.IsBangumi ? "pgc/player/web/playurl" : "x/player/playurl";
        var root = await LoadAsync($"https://api.bilibili.com/{route}?bvid={episode.Bvid}&cid={episode.Cid}&qn={qn}&fnver=0&fnval=4048&fourk=1", Referer(episode), ct);
        var data = root.Get(episode.IsBangumi ? "result" : "data");
        if (data.Bool("is_preview") || data.Int("error_code") == -10403) throw new InvalidDataException("当前账号只能获取预览片段，请确认会员、购片或版权权限");
        return data;
    }
    public async Task<List<Quality>> QualitiesAsync(Episode episode, DownloadMode mode, CancellationToken ct = default)
    {
        var data = await PlayAsync(episode, 127, ct);
        if (mode == DownloadMode.Audio)
        {
            var audio = AudioCandidates(data);
            if (audio.Count > 0) return audio.Select(a => new Quality(a.Item.Int("id"), a.Label, "保存为 M4A，保留源编码" )).ToList();
            if (data.Get("dash").ValueKind is not JsonValueKind.Object && Durls(data).Any()) return [new(data.Int("quality", 1), "源音轨", "此资源需要先下载音画合流，再提取音轨")];
            throw new InvalidDataException("此资源没有可下载的音轨");
        }
        var labels = Labels(data);
        var available = data.Get("dash").Get("video").Items().Where(HasUrl).Select(v => v.Int("id")).Where(q => q > 0).Distinct().OrderDescending().ToList();
        if (available.Count == 0 && Durls(data).Any()) available.Add(data.Int("quality"));
        if (available.Count == 0) throw new InvalidDataException("当前账号未获取到可下载画质，请确认登录和视频权限");
        var restricted = labels.Where(pair => !available.Contains(pair.Key)).Select(pair => pair.Value);
        var note = $"当前最高可下载 {labels.GetValueOrDefault(available[0], $"QN {available[0]}")}";
        if (restricted.Any()) note += $"\n未获取到 {string.Join("、", restricted)}，可能受登录、会员或视频权限影响";
        return available.Select(q => new Quality(q, labels.GetValueOrDefault(q, $"QN {q}"), note)).ToList();
    }
    public async Task<StreamSet> StreamsAsync(Episode episode, Quality quality, DownloadMode mode, CancellationToken ct)
    {
        var data = await PlayAsync(episode, mode == DownloadMode.Audio ? 127 : quality.Qn, ct);
        var dash = data.Get("dash");
        var referer = Referer(episode);
        if (dash.ValueKind != JsonValueKind.Object)
        {
            var parts = Durls(data).ToList();
            if (parts.Count != 1) throw new InvalidDataException("当前资源返回分段流，暂不支持此格式");
            if (mode != DownloadMode.Audio) CheckQuality(quality.Qn, data.Int("quality"));
            var item = parts[0];
            var stream = new MediaStream(mode == DownloadMode.Audio ? "audio" : "video", Urls(item, true), referer, item.Long("size"));
            return mode == DownloadMode.Audio ? new(null, stream) : new(stream, null);
        }
        if (mode == DownloadMode.Audio)
        {
            var candidate = AudioCandidates(data).FirstOrDefault(a => a.Item.Int("id") == quality.Qn);
            if (candidate.Item.ValueKind == JsonValueKind.Undefined) throw new InvalidDataException("所选音频品质已不可用，请刷新品质后重试");
            return new(null, new MediaStream("audio", Urls(candidate.Item), referer));
        }
        var videos = dash.Get("video").Items().Where(v => v.Int("id") == quality.Qn && HasUrl(v)).ToList();
        if (videos.Count == 0) throw new InvalidDataException("所选画质已不可用，请重新选择，任务已停止");
        // Prefer AVC for broad Windows player compatibility when multiple codecs share a quality.
        var video = videos.OrderBy(v => v.Int("codecid") == 7 ? 0 : 1).ThenByDescending(v => v.Long("bandwidth")).First();
        MediaStream? audioStream = null;
        if (mode == DownloadMode.Combined)
        {
            var audio = dash.Get("audio").Items().Where(HasUrl).OrderByDescending(a => a.Long("bandwidth")).FirstOrDefault();
            if (audio.ValueKind != JsonValueKind.Undefined) audioStream = new MediaStream("audio", Urls(audio), referer);
        }
        return new(new MediaStream("video", Urls(video), referer), audioStream);
    }
    private static void CheckQuality(int requested, int actual)
    {
        if (requested != actual) throw new InvalidDataException($"所选画质 QN {requested} 当前不可用，接口返回 QN {actual}，请重新选择");
    }
    private static Dictionary<int, string> Labels(JsonElement data)
    {
        var qualities = data.Get("accept_quality").Items().ToList();
        var descriptions = data.Get("accept_description").Items().ToList();
        var labels = new Dictionary<int, string>();
        for (var i = 0; i < Math.Min(qualities.Count, descriptions.Count); i++) labels[qualities[i].GetInt32()] = descriptions[i].GetString() ?? "";
        foreach (var format in data.Get("support_formats").Items()) labels.TryAdd(format.Int("quality"), format.Text("new_description", format.Text("description")));
        return labels;
    }
    private static List<(JsonElement Item, string Label)> AudioCandidates(JsonElement data)
    {
        var dash = data.Get("dash");
        var result = new List<(JsonElement Item, string Label)>();
        result.AddRange(dash.Get("audio").Items().Where(HasUrl).Select(a => (a, $"音频 {a.Long("bandwidth") / 1000} kbps")));
        result.AddRange(dash.Get("dolby").Get("audio").Items().Where(HasUrl).Select(a => (a, "Dolby 音频")));
        var flac = dash.Get("flac").Get("audio");
        if (HasUrl(flac)) result.Add((flac, "FLAC 无损"));
        return result.DistinctBy(a => a.Item.Int("id")).OrderByDescending(a => a.Item.Long("bandwidth")).ToList();
    }
    private static IEnumerable<JsonElement> Durls(JsonElement data) => data.Get("durl").ValueKind == JsonValueKind.Array ? data.Get("durl").Items() : data.Get("durls").Items();
    private static bool HasUrl(JsonElement item) => item.Text("baseUrl", item.Text("base_url")).Length > 0;
    private static List<Uri> Urls(JsonElement item, bool durl = false)
    {
        var first = item.Text(durl ? "url" : "baseUrl", item.Text("base_url"));
        var backups = item.Get("backup_url").ValueKind == JsonValueKind.Array ? item.Get("backup_url") : item.Get("backupUrl");
        return new[] { first }.Concat(backups.Items().Select(v => v.GetString() ?? "")).Where(v => v.Length > 0).Select(Network.MediaUri).Distinct().ToList();
    }
    public static string Referer(Episode episode) => $"https://www.bilibili.com/video/{episode.Bvid}";
    [GeneratedRegex(@"https?://[-a-zA-Z0-9+&@#/%?=~_|!:,.;]+", RegexOptions.IgnoreCase)] private static partial Regex UrlPattern();
    [GeneratedRegex(@"(?<![A-Za-z0-9])(?:BV[0-9A-Za-z]{10}|ep\d+|ss\d+)(?![A-Za-z0-9])")] private static partial Regex IdPattern();
}
