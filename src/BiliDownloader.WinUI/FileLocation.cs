using System.Runtime.InteropServices;

namespace BiliDownloader.WinUI;

internal static class FileLocation
{
    // Resolve the shell item off the UI thread; the save directory may be on a network drive.
    public static void Reveal(string path)
    {
        if (!File.Exists(path)) throw new FileNotFoundException();

        Marshal.ThrowExceptionForHR(CoInitializeEx(0, 0)); // COINIT_MULTITHREADED
        nint item = 0;
        try
        {
            Marshal.ThrowExceptionForHR(SHParseDisplayName(path, 0, out item, 0, 0));
            // With no child array, an absolute item ID opens that item's parent and selects it.
            Marshal.ThrowExceptionForHR(SHOpenFolderAndSelectItems(item, 0, 0, 0));
        }
        finally
        {
            Marshal.FreeCoTaskMem(item);
            CoUninitialize();
        }
    }

    [DllImport("ole32.dll", ExactSpelling = true)]
    private static extern int CoInitializeEx(nint reserved, uint flags);

    [DllImport("ole32.dll", ExactSpelling = true)]
    private static extern void CoUninitialize();

    [DllImport("shell32.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int SHParseDisplayName(string name, nint bindContext, out nint item, uint attributesIn, nint attributesOut);

    [DllImport("shell32.dll", ExactSpelling = true)]
    private static extern int SHOpenFolderAndSelectItems(nint item, uint itemCount, nint children, uint flags);
}
