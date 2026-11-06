using System.Text.RegularExpressions;

namespace BiliDownloader.Core;

public static partial class Naming
{
    public static string SafeFilename(string? name)
    {
        var clean = InvalidCharacters().Replace(name ?? "", "_").Trim().TrimEnd('.', ' ');
        if (clean.Length == 0) return "untitled";
        if (ReservedName().IsMatch(clean)) clean = "_" + clean;
        if (clean.Length > 180) clean = clean[..180];
        if (char.IsHighSurrogate(clean[^1])) clean = clean[..^1];
        return clean.TrimEnd('.', ' ') is { Length: > 0 } result ? result : "untitled";
    }
    public static string Render(string template, MediaResult media, Episode episode, Quality quality, int index)
    {
        var values = new Dictionary<string, string>
        {
            ["title"] = episode.Title, ["media"] = media.Title, ["quality"] = quality.Label,
            ["bvid"] = episode.Bvid, ["cid"] = episode.Cid.ToString(),
            ["index"] = index.ToString(media.Episodes.Count >= 10 ? "D2" : "D"), ["total"] = media.Episodes.Count.ToString()
        };
        if (template.Length > 512) return SafeFilename(episode.Title);
        var rendered = TemplateField().Replace(template, m =>
        {
            var key = m.Groups[1].Value;
            if (!values.TryGetValue(key, out var value)) return m.Value;
            var format = m.Groups[2].Value;
            if (format.Length == 0) return value;
            if (key == "index" && Regex.IsMatch(format, @"^0?[1-9][0-9]?d$"))
                return index.ToString("D" + int.Parse(format.TrimEnd('d')));
            return value;
        });
        return SafeFilename(rendered);
    }
    public static string AvailablePath(string path, ISet<string>? reserved = null)
    {
        var full = Path.GetFullPath(path);
        var stem = Path.GetFileNameWithoutExtension(full);
        var extension = Path.GetExtension(full);
        var candidate = full;
        for (var i = 1; File.Exists(candidate) || (reserved?.Contains(candidate) ?? false); i++)
            candidate = Path.Combine(Path.GetDirectoryName(full)!, $"{stem} ({i}){extension}");
        return candidate;
    }
    [GeneratedRegex("[\\\\/:*?\"<>|\\x00-\\x1F]")] private static partial Regex InvalidCharacters();
    [GeneratedRegex(@"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", RegexOptions.IgnoreCase)] private static partial Regex ReservedName();
    [GeneratedRegex(@"\{([a-z]+)(?::([^{}]+))?\}")] private static partial Regex TemplateField();
}
