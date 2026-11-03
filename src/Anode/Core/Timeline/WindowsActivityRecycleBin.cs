using System.Runtime.InteropServices;

namespace Anode.Core.Timeline;

/// <summary>Windows 8+ explicit recycle operation, with no alternate deletion implementation.</summary>
internal sealed class WindowsActivityRecycleBin : IActivityRecycleBin
{
    // Verified against Microsoft's ShObjIdl_core.h and IFileOperation::SetOperationFlags.
    // RECYCLEONDELETE | EARLYFAILURE | NOERRORUI | SILENT | NOCONFIRMATION |
    // WANTNUKEWARNING | NO_CONNECTED_ELEMENTS. Do not substitute ALLOWUNDO (only "if possible").
    internal const uint OperationFlags = 0x00080000 | 0x00100000 | 0x0400 | 0x0004 | 0x0010 | 0x4000 | 0x2000;
    private static readonly Guid FileOperationClass = new("3ad05575-8857-4850-9277-11b85bdb8e09");
    private static readonly Guid ShellItemInterface = new("43826d1e-e718-42ee-bc55-a1e261c37bfe");

    public void Validate(string path)
    {
        if (!Path.IsPathFullyQualified(path) || path.StartsWith(@"\\", StringComparison.Ordinal)
            || new DriveInfo(Path.GetPathRoot(path)!).DriveType != DriveType.Fixed)
            throw new IOException("Activity cleanup only supports physical files on a local fixed drive with Windows recycling.");
        if (!File.Exists(path) || (File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0)
            throw new IOException("Recycle target is missing, a directory or a file link.");
    }

    public void Recycle(string path)
    {
        Validate(path);
        var operation = CreateOperation(); IntPtr item = IntPtr.Zero;
        try
        {
            Guid iid = ShellItemInterface;
            Marshal.ThrowExceptionForHR(SHCreateItemFromParsingName(path, IntPtr.Zero, ref iid, out item));
            operation.DeleteItem(item, IntPtr.Zero);
            operation.PerformOperations();
            operation.GetAnyOperationsAborted(out bool aborted);
            if (aborted) throw new IOException("Windows aborted the recycle operation; cleanup has stopped.");
        }
        finally
        {
            if (item != IntPtr.Zero) Marshal.Release(item);
            Marshal.FinalReleaseComObject(operation);
        }
    }

    public bool OriginalExists(string path) => File.Exists(path);

    /// <summary>Activate COM and set recycling flags without queuing or performing a file operation.</summary>
    internal static bool Probe()
    {
        var operation = CreateOperation();
        try { operation.GetAnyOperationsAborted(out bool aborted); return !aborted; }
        finally { Marshal.FinalReleaseComObject(operation); }
    }

    private static IFileOperation CreateOperation()
    {
        if (Thread.CurrentThread.GetApartmentState() != ApartmentState.STA)
            throw new InvalidOperationException("Windows recycling requires the dedicated STA worker.");
        var operation = (IFileOperation)Activator.CreateInstance(Type.GetTypeFromCLSID(FileOperationClass, throwOnError: true)!)!;
        try { operation.SetOperationFlags(OperationFlags); return operation; }
        catch { Marshal.FinalReleaseComObject(operation); throw; }
    }

    internal static Task<T> OnStaAsync<T>(Func<T> action)
    {
        var completion = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        var worker = new Thread(() =>
        {
            try { completion.SetResult(action()); }
            catch (Exception ex) { completion.SetException(ex); }
        }) { IsBackground = true, Name = "Anode activity recycle" };
        worker.SetApartmentState(ApartmentState.STA); worker.Start();
        return completion.Task;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int SHCreateItemFromParsingName(string path, IntPtr bindingContext, ref Guid iid, out IntPtr item);

    // Preserve the complete SDK vtable order, including unused methods. Pointer-only arguments
    // represent unused native interfaces; only flags, DeleteItem, PerformOperations and aborted are called.
    [ComImport, Guid("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileOperation
    {
        void Advise(IntPtr sink, out uint cookie);
        void Unadvise(uint cookie);
        void SetOperationFlags(uint flags);
        void SetProgressMessage([MarshalAs(UnmanagedType.LPWStr)] string message);
        void SetProgressDialog(IntPtr dialog);
        void SetProperties(IntPtr properties);
        void SetOwnerWindow(IntPtr window);
        void ApplyPropertiesToItem(IntPtr item);
        void ApplyPropertiesToItems(IntPtr items);
        void RenameItem(IntPtr item, [MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr sink);
        void RenameItems(IntPtr items, [MarshalAs(UnmanagedType.LPWStr)] string name);
        void MoveItem(IntPtr item, IntPtr destination, [MarshalAs(UnmanagedType.LPWStr)] string? name, IntPtr sink);
        void MoveItems(IntPtr items, IntPtr destination);
        void CopyItem(IntPtr item, IntPtr destination, [MarshalAs(UnmanagedType.LPWStr)] string? name, IntPtr sink);
        void CopyItems(IntPtr items, IntPtr destination);
        void DeleteItem(IntPtr item, IntPtr sink);
        void DeleteItems(IntPtr items);
        void NewItem(IntPtr destination, uint attributes, [MarshalAs(UnmanagedType.LPWStr)] string name,
            [MarshalAs(UnmanagedType.LPWStr)] string? templateName, IntPtr sink);
        void PerformOperations();
        void GetAnyOperationsAborted([MarshalAs(UnmanagedType.Bool)] out bool aborted);
    }
}
