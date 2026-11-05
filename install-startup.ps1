# Starts aw-watcher-anydesk hidden at every Windows logon, independent of
# aw-qt's PATH-based module discovery. Run once:  powershell -File install-startup.ps1
# Remove it with:                                  powershell -File install-startup.ps1 -Remove
param([switch]$Remove, [string]$ExePath)

$startup = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startup "aw-watcher-anydesk.vbs"

if ($Remove) {
    Remove-Item $launcher -ErrorAction SilentlyContinue
    Write-Host "Removed $launcher"
    return
}

$exe = $ExePath
if (-not $exe) { $exe = (Get-Command aw-watcher-anydesk -ErrorAction SilentlyContinue).Source }
if (-not $exe) {
    # Not on this terminal's PATH: look in the usual pip install locations.
    $patterns = @("$env:APPDATA\Python\Python*\Scripts\aw-watcher-anydesk.exe",
                  "$env:LOCALAPPDATA\Programs\Python\Python*\Scripts\aw-watcher-anydesk.exe",
                  "$env:ProgramFiles\Python*\Scripts\aw-watcher-anydesk.exe")
    $exe = $patterns | ForEach-Object { Get-ChildItem $_ -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $exe -or -not (Test-Path $exe)) {
    Write-Host "Searched:"
    $patterns | ForEach-Object { Write-Host "  $_" }
    throw "aw-watcher-anydesk.exe not found. Run 'pip install .' first, or pass -ExePath <full path to the exe>."
}

$vbs = 'CreateObject("Wscript.Shell").Run """' + $exe + '""", 0, False'
[IO.File]::WriteAllText($launcher, $vbs + "`r`n", (New-Object Text.ASCIIEncoding))
Write-Host "Created $launcher"
Write-Host "It will start the watcher at the next logon. To start it now: wscript `"$launcher`""
