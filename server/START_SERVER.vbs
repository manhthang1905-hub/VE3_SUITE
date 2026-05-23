Option Explicit

Dim fso, shell, root, cmd, pyExe
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

Function FirstLine(ByVal text)
    Dim arr
    arr = Split(Replace(text, vbCr, ""), vbLf)
    If UBound(arr) >= 0 Then
        FirstLine = Trim(arr(0))
    Else
        FirstLine = ""
    End If
End Function

Function FindInPath(ByVal exeName)
    On Error Resume Next
    Dim e, out
    Set e = shell.Exec("cmd /c where " & exeName & " 2>nul")
    out = ""
    If Not e Is Nothing Then out = e.StdOut.ReadAll()
    FindInPath = FirstLine(out)
End Function

' 1) Prefer local venv interpreter (if present)
pyExe = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyExe) Then pyExe = root & "\venv\Scripts\pythonw.exe"

' 2) Fallback to PATH
If Not fso.FileExists(pyExe) Then pyExe = FindInPath("pythonw.exe")
If pyExe = "" Then pyExe = FindInPath("pyw.exe")
If pyExe = "" Then pyExe = FindInPath("py.exe")

If pyExe = "" Then
    MsgBox "Khong tim thay pythonw/pyw/py de chay tool." & vbCrLf & _
           "Hay cai Python hoac mo tool bang CMD roi chay: python -u server\server_gui.py", _
           vbExclamation, "START_SERVER"
    WScript.Quit 1
End If

' Run hidden (window style 0), non-blocking.
If LCase(Right(pyExe, 6)) = "py.exe" Then
    cmd = """" & pyExe & """ -3 -u """ & root & "\server\server_gui.py"""
Else
    cmd = """" & pyExe & """ -u """ & root & "\server\server_gui.py"""
End If
shell.Run cmd, 0, False
