using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text.Json.Nodes;
using Anode.Core.Util;

namespace Anode.Daemon;

// Passwords never become managed strings or travel through CLI/MCP/config/logs.
internal sealed class LoginSecret : IDisposable
{
    private IntPtr _bstr;
    internal string UserName { get; }
    internal int ByteLength { get; }
    internal IntPtr Password => _bstr != IntPtr.Zero ? _bstr : throw new ObjectDisposedException(nameof(LoginSecret));
    internal LoginSecret(string userName, IntPtr utf16, int bytes)
    {
        if (string.IsNullOrWhiteSpace(userName) || bytes <= 0 || bytes > 2560 || bytes % 2 != 0)
            throw new InvalidOperationException("Enter the Windows account name and password (not a PIN).");
        UserName = userName;
        ByteLength = bytes;
        _bstr = SysAllocStringLen(utf16, (uint)(bytes / 2));
        if (_bstr == IntPtr.Zero) throw new OutOfMemoryException();
    }
    internal LoginSecret Copy() => new(UserName, Password, ByteLength);
    public void Dispose()
    {
        var value = Interlocked.Exchange(ref _bstr, IntPtr.Zero);
        if (value != IntPtr.Zero) Marshal.ZeroFreeBSTR(value);
        GC.SuppressFinalize(this);
    }
    ~LoginSecret() => Dispose();
    [DllImport("oleaut32.dll", ExactSpelling = true)]
    private static extern IntPtr SysAllocStringLen(IntPtr text, uint length);
}

internal interface ILoginVault
{
    LoginSecret? ReadLatest();
    void Append(LoginSecret credential);
}

internal sealed record LoginChoice(LoginSecret Credential, bool Save);

// The success event, not the checkbox or transport Connected event, commits saving.
internal sealed class RememberedLogin : IDisposable
{
    private readonly ILoginVault _vault;
    private readonly Func<IntPtr, LoginChoice?> _prompt;
    private LoginSecret? _pending;
    private bool _attempting;
    private bool _rejected;
    private string _state = "not-used";
    private string? _source;
    internal RememberedLogin(ILoginVault? vault = null, Func<IntPtr, LoginChoice?>? prompt = null)
    {
        _vault = vault ?? new WindowsLoginVault();
        _prompt = prompt ?? WindowsLoginPrompt.Show;
    }
    internal JsonObject Status() => new() { ["state"] = _state, ["source"] = _source,
        ["storage"] = "Windows Credential Manager", ["secretExportAvailable"] = false };
    internal LoginSecret? Prepare(bool enabled, bool force, IntPtr owner)
    {
        ClearPending();
        _attempting = false;
        _source = null;
        if (!enabled) { _state = "disabled"; return null; }
        if (_rejected && !force)
            throw new InvalidOperationException("Saved sign-in failed. Click Sign in to enter the current password; automatic retries are disabled.");
        if (!force && _vault.ReadLatest() is { } saved)
        {
            _attempting = true; _state = "using-saved"; _source = "saved";
            return saved;
        }
        _state = "awaiting-user";
        var choice = _prompt(owner);
        if (choice is null) { _state = "cancelled"; throw new OperationCanceledException("Windows sign-in was cancelled; no credential was saved."); }
        _rejected = false;
        _attempting = true; _source = "native-dialog";
        if (choice.Save) _pending = choice.Credential.Copy();
        _state = choice.Save ? "awaiting-login-before-save" : "one-time-login";
        return choice.Credential;
    }
    internal void LoginComplete()
    {
        if (!_attempting) return;
        _attempting = false;
        try
        {
            if (_pending is not null) { _vault.Append(_pending); _state = "saved"; }
            else _state = _source == "saved" ? "reused" : "not-saved";
        }
        catch (Exception error)
        {
            _state = "save-failed";
            // Do not include exception text: custom providers may include input data.
            Log.Warn($"Windows login succeeded, but saving failed ({error.GetType().Name}). Use Sign in to save again.");
        }
        finally { ClearPending(); }
    }
    internal void Failed(bool credentialsRejected = false)
    {
        if (_attempting)
        {
            _rejected |= credentialsRejected;
            _state = credentialsRejected ? "login-failed" : "connection-failed";
        }
        _attempting = false;
        ClearPending();
    }
    private void ClearPending() { _pending?.Dispose(); _pending = null; }
    public void Dispose() => ClearPending();
}

// Versioned entries preserve earlier recovery material. No credential deletion or replacement.
internal sealed class WindowsLoginVault : ILoginVault
{
    private readonly string _prefix;
    private readonly uint _persistence;
    internal WindowsLoginVault(string? testPrefix = null, uint persistence = 2)
    {
        using var identity = WindowsIdentity.GetCurrent();
        _prefix = testPrefix ?? $"Anode/WindowsLogin/v1/{Environment.MachineName}/{identity.User!.Value}/{Env.Channel}/localhost/";
        _persistence = persistence; // 2 = current user, this machine; never roam.
    }
    public unsafe LoginSecret? ReadLatest()
    {
        if (!CredEnumerateW(_prefix + "*", 0, out uint count, out IntPtr list))
        {
            int code = Marshal.GetLastWin32Error();
            if (code == 1168) return null;
            throw new Win32Exception(code, "Cannot read Anode's saved Windows login.");
        }
        try
        {
            NativeCredential? latest = null;
            for (int i = 0; i < count; i++)
            {
                var item = Marshal.PtrToStructure<NativeCredential>(Marshal.ReadIntPtr(list, i * IntPtr.Size));
                if (item.Type != 1 || item.TargetName is null || !item.TargetName.StartsWith(_prefix, StringComparison.OrdinalIgnoreCase)) continue;
                if (latest is null || item.LastWritten > latest.Value.LastWritten) latest = item;
            }
            return latest is { } value ? new LoginSecret(value.UserName!, value.Blob, checked((int)value.BlobSize)) : null;
        }
        finally
        {
            for (int i = 0; i < count; i++)
            {
                var item = Marshal.PtrToStructure<NativeCredential>(Marshal.ReadIntPtr(list, i * IntPtr.Size));
                if (item.Blob != IntPtr.Zero && item.BlobSize > 0)
                    CryptographicOperations.ZeroMemory(new Span<byte>((void*)item.Blob, checked((int)item.BlobSize)));
            }
            CredFree(list);
        }
    }
    public void Append(LoginSecret credential)
    {
        var value = new NativeCredential { Type = 1,
            TargetName = _prefix + Guid.NewGuid().ToString("N"),
            Comment = "Anode local child desktop; saved after successful Windows login",
            UserName = credential.UserName, Blob = credential.Password,
            BlobSize = (uint)credential.ByteLength, Persist = _persistence };
        if (!CredWriteW(ref value, 0))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Windows could not save the Anode login.");
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NativeCredential
    {
        public uint Flags, Type;
        [MarshalAs(UnmanagedType.LPWStr)] public string? TargetName;
        [MarshalAs(UnmanagedType.LPWStr)] public string? Comment;
        public long LastWritten;
        public uint BlobSize;
        public IntPtr Blob;
        public uint Persist, AttributeCount;
        public IntPtr Attributes;
        [MarshalAs(UnmanagedType.LPWStr)] public string? TargetAlias;
        [MarshalAs(UnmanagedType.LPWStr)] public string? UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredEnumerateW(string filter, uint flags, out uint count, out IntPtr credentials);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredWriteW(ref NativeCredential credential, uint flags);
    [DllImport("advapi32.dll", ExactSpelling = true)] private static extern void CredFree(IntPtr credentials);
}

internal static class WindowsLoginPrompt
{
    internal static LoginChoice? Show(IntPtr owner)
    {
        var info = new PromptInfo { Size = Marshal.SizeOf<PromptInfo>(), Owner = owner,
            Caption = "Anode 本机子桌面登录",
            Message = "输入当前 Windows 账户密码（不是 PIN）。勾选保存后，登录成功才由 Windows 凭据管理器保管，供本机 Anode 自动登录。密码不会发送到聊天。" };
        uint package = 0;
        bool save = true;
        IntPtr output = IntPtr.Zero;
        uint bytes = 0;
        try
        {
            uint result = CredUIPromptForWindowsCredentialsW(ref info, 0, ref package, IntPtr.Zero, 0,
                out output, out bytes, ref save, 3); // CREDUIWIN_GENERIC | CREDUIWIN_CHECKBOX
            if (result == 1223) return null;
            if (result != 0) throw new Win32Exception((int)result, "Windows sign-in dialog failed.");
            return new LoginChoice(Unpack(output, bytes), save);
        }
        finally
        {
            if (output != IntPtr.Zero) { Clear(output, bytes); Marshal.FreeCoTaskMem(output); }
        }
    }
    internal static LoginSecret Unpack(IntPtr packed, uint bytes)
    {
        uint userChars = 0, domainChars = 0, passwordChars = 0;
        bool sized = CredUnPackAuthenticationBufferW(0, packed, bytes, IntPtr.Zero, ref userChars,
            IntPtr.Zero, ref domainChars, IntPtr.Zero, ref passwordChars);
        if (sized || Marshal.GetLastWin32Error() != 122 || userChars is 0 or > 1024 || domainChars > 1024 || passwordChars is 0 or > 1281)
            throw new InvalidOperationException("Windows did not return a supported account password.");
        using var user = new ClearedBuffer(userChars * 2);
        using var domain = new ClearedBuffer(Math.Max(domainChars, 1) * 2);
        using var password = new ClearedBuffer(passwordChars * 2);
        if (!CredUnPackAuthenticationBufferW(0, packed, bytes, user.Pointer, ref userChars,
                domain.Pointer, ref domainChars, password.Pointer, ref passwordChars))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Windows could not unpack the account login.");
        string name = Marshal.PtrToStringUni(user.Pointer) ?? "";
        string domainName = Marshal.PtrToStringUni(domain.Pointer) ?? "";
        if (!name.Contains('\\') && !string.IsNullOrEmpty(domainName)) name = domainName + "\\" + name;
        int length = 0;
        while (length < password.Size / 2 && Marshal.ReadInt16(password.Pointer, length * 2) != 0) length++;
        if (length == password.Size / 2) throw new InvalidOperationException("Invalid Windows password buffer.");
        return new LoginSecret(name, password.Pointer, length * 2);
    }
    private static unsafe void Clear(IntPtr pointer, uint bytes) =>
        CryptographicOperations.ZeroMemory(new Span<byte>((void*)pointer, checked((int)bytes)));
    private sealed class ClearedBuffer : IDisposable
    {
        internal IntPtr Pointer { get; }
        internal uint Size { get; }
        internal ClearedBuffer(uint bytes) { Size = bytes; Pointer = Marshal.AllocHGlobal((int)bytes); Clear(Pointer, bytes); }
        public void Dispose() { Clear(Pointer, Size); Marshal.FreeHGlobal(Pointer); }
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct PromptInfo
    {
        public int Size;
        public IntPtr Owner;
        [MarshalAs(UnmanagedType.LPWStr)] public string Message;
        [MarshalAs(UnmanagedType.LPWStr)] public string Caption;
        public IntPtr Banner;
    }
    [DllImport("credui.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint CredUIPromptForWindowsCredentialsW(ref PromptInfo info, uint error, ref uint package,
        IntPtr input, uint inputBytes, out IntPtr output, out uint outputBytes,
        [MarshalAs(UnmanagedType.Bool)] ref bool save, uint flags);
    [DllImport("credui.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredUnPackAuthenticationBufferW(uint flags,
        IntPtr buffer, uint bytes, IntPtr user, ref uint userChars, IntPtr domain, ref uint domainChars,
        IntPtr password, ref uint passwordChars);
}
