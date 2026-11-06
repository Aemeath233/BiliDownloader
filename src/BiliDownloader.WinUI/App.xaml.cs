using Microsoft.UI.Xaml;
using BiliDownloader.Core;

namespace BiliDownloader.WinUI;

public partial class App : Application
{
#if !UI_REGRESSION
    private Window? window;
    private SingleInstance? instance;
#endif
    public App()
    {
#if UI_REGRESSION
        UnhandledException += (_, args) =>
        {
            var arguments = Environment.GetCommandLineArgs();
            var report = arguments[Array.IndexOf(arguments, "--report") + 1];
            System.IO.File.WriteAllText(report, System.Text.Json.JsonSerializer.Serialize(new { passed = false, error = args.Exception.ToString(), message = args.Message }));
        };
#else
        UnhandledException += (_, args) =>
        {
            try
            {
                var arguments = Environment.GetCommandLineArgs();
                var profileIndex = Array.IndexOf(arguments, "--profile");
                var profile = profileIndex >= 0 && profileIndex + 1 < arguments.Length ? arguments[profileIndex + 1]
                    : System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "BiliDownloader");
                var directory = System.IO.Path.Combine(profile, "logs");
                System.IO.Directory.CreateDirectory(directory);
                System.IO.File.AppendAllText(System.IO.Path.Combine(directory, "startup-error.log"), Network.SafeError(args.Exception) + Environment.NewLine + args.Exception.StackTrace + Environment.NewLine);
            }
            catch (System.IO.IOException) { }
            catch (UnauthorizedAccessException) { }
        };
#endif
        InitializeComponent();
    }
    protected override async void OnLaunched(LaunchActivatedEventArgs args)
    {
#if UI_REGRESSION
        await WinUIRegression.RunAsync();
        Exit();
        return;
#else
        var arguments = Environment.GetCommandLineArgs();
        var profileIndex = Array.IndexOf(arguments, "--profile");
        var profile = profileIndex >= 0 && profileIndex + 1 < arguments.Length ? arguments[profileIndex + 1] : null;
        if (profile is null)
        {
            instance = new SingleInstance();
            if (!instance.Acquire()) { await SingleInstance.ActivateExistingAsync(); Exit(); return; }
        }
        var main = new MainWindow(profile);
        window = main;
        if (instance is not null) instance.Activated += () => main.DispatcherQueue.TryEnqueue(main.ShowWindow);
        window.Closed += (_, _) => instance?.Dispose();
        window.Activate();
#endif
    }
}
