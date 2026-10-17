# SPDX-License-Identifier: GPL-2.0-only
# Windows-only diagnostic workaround for the VBoxUSB 7.2.2 speed cache.
# Run while the OCTA-CAPTURE is attached to WSL, with audio streams stopped.
# GET_DEVICE queries metadata and initializes the driver's high-speed flag.
# No device claim, USB reset, persistent setting or driver installation.
[CmdletBinding()]
param(
    [string] $BusId = '',
    [switch] $CompileOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not ('OctaCapture.UsbipdSpeedCache' -as [type])) {
    Add-Type -TypeDefinition @'
// SPDX-License-Identifier: GPL-2.0-only
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace OctaCapture
{
    public static class UsbipdSpeedCache
    {
        private const uint GetDevice = 0x22980c;
        private const uint MaximumListCharacters = 65536;

        [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern uint CM_Get_Device_Interface_List_SizeW(
            out uint size, ref Guid interfaceClass, string instanceId, uint flags);

        [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern uint CM_Get_Device_Interface_ListW(
            ref Guid interfaceClass, string instanceId, [Out] char[] buffer, uint size, uint flags);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
        private static extern SafeFileHandle CreateFileW(
            string name, uint access, uint sharing, IntPtr security, uint disposition,
            uint attributes, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool DeviceIoControl(
            SafeFileHandle handle, uint code, ref IntPtr input, uint inputSize,
            out IntPtr output, uint outputSize, out uint returned, IntPtr overlapped);

        public static void Initialize(string instanceId)
        {
            if (String.IsNullOrWhiteSpace(instanceId))
                throw new ArgumentException("Missing attached device instance ID.", "instanceId");
            if (Environment.OSVersion.Platform != PlatformID.Win32NT || !Environment.Is64BitProcess)
                throw new PlatformNotSupportedException("Use 64-bit PowerShell on Windows.");

            Guid interfaceClass = new Guid("00873fdf-cafe-80ee-aa5e-00c04fb1720b");
            uint size;
            uint status = CM_Get_Device_Interface_List_SizeW(out size, ref interfaceClass, instanceId, 0);
            if (status != 0 || size < 2 || size > MaximumListCharacters)
                throw new InvalidOperationException("Cannot size attached VBoxUSB interface list: " + status);
            char[] buffer = new char[size];
            status = CM_Get_Device_Interface_ListW(ref interfaceClass, instanceId, buffer, size, 0);
            if (status != 0)
                throw new InvalidOperationException("Cannot read attached VBoxUSB interface list: " + status);
            string[] paths = new String(buffer).Split(new char[] { '\0' }, StringSplitOptions.RemoveEmptyEntries);
            if (paths.Length != 1)
                throw new InvalidOperationException("Expected exactly one attached VBoxUSB interface.");

            const uint readWrite = 3; // FILE_READ_DATA | FILE_WRITE_DATA
            const uint shareReadWrite = 3;
            const uint openExisting = 3;
            using (SafeFileHandle handle = CreateFileW(paths[0], readWrite, shareReadWrite,
                IntPtr.Zero, openExisting, 0, IntPtr.Zero))
            {
                if (handle.IsInvalid)
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Opening attached VBoxUSB interface");
                IntPtr input = IntPtr.Zero;
                IntPtr output;
                uint returned;
                uint length = checked((uint)IntPtr.Size);
                if (!DeviceIoControl(handle, GetDevice, ref input, length, out output, length,
                    out returned, IntPtr.Zero))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "VBoxUSB GET_DEVICE");
                if (returned != length)
                    throw new InvalidOperationException("VBoxUSB GET_DEVICE returned an unexpected size.");
            }
        }
    }
}
'@
}

if ($CompileOnly) {
    Write-Output 'PowerShell helper and embedded C# compiled; no Windows API was called.'
    return
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Run this script in Windows PowerShell, outside WSL.'
}
if ($BusId -and $BusId -notmatch '^[1-9][0-9]*-[1-9][0-9]*$') {
    throw 'Invalid USB/IP bus ID.'
}
$stateJson = & usbipd state
if ($LASTEXITCODE -ne 0) { throw 'usbipd state failed.' }
$state = $stateJson | ConvertFrom-Json
$devices = @($state.Devices | Where-Object {
    $_.InstanceId -match '^USB\\VID_0582&PID_0120\\' -and
    $_.ClientIPAddress -and $_.StubInstanceId -and
    (-not $BusId -or $_.BusId -eq $BusId)
})
if ($devices.Count -ne 1) {
    throw 'Expected exactly one attached OCTA-CAPTURE. Attach it to WSL first; use -BusId if needed.'
}
[OctaCapture.UsbipdSpeedCache]::Initialize($devices[0].StubInstanceId)
Write-Output ('VBoxUSB GET_DEVICE completed for OCTA-CAPTURE on bus {0}. Rerun Linux audio checks.' -f $devices[0].BusId)
