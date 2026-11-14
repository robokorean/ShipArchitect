Option Explicit
Dim fs, shell, folder, runner, command
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
runner = folder & "\START_WINDOWS.cmd"
command = Chr(34) & runner & Chr(34) & " --gui"
If shell.Run(command, 0, True) <> 0 Then
    MsgBox "Ship Architect startup failed. Run START_WINDOWS.cmd to see details.", 16, "Ship Architect"
End If
