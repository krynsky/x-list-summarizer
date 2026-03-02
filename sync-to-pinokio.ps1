param()
# sync-to-pinokio.ps1
# Watches the workspace app\ folder and instantly copies any changed .py file to Pinokio.
# Start this once in a terminal, then keep it running alongside Pinokio.

$source = "d:\My Data\My Apps\X List Summarizer\x-list-summarizer\app"
$dest   = "C:\AI\pinokio\api\x-list-summarizer.git\app"

if (-not (Test-Path $dest)) {
    Write-Host "ERROR: Pinokio app folder not found at: $dest" -ForegroundColor Red
    exit 1
}

Write-Host "Watching: $source" -ForegroundColor Cyan
Write-Host "Syncing to: $dest" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop.`n"

function Sync-File($filePath) {
    $name = Split-Path $filePath -Leaf
    $target = Join-Path $dest $name
    Start-Sleep -Milliseconds 300
    try {
        $bytes = [System.IO.File]::ReadAllBytes($filePath)
        [System.IO.File]::WriteAllBytes($target, $bytes)
        Write-Host "[$((Get-Date).ToString('HH:mm:ss'))] Synced: $name" -ForegroundColor Green
    } catch {
        Write-Host "[$((Get-Date).ToString('HH:mm:ss'))] WARN: Could not sync ${name}: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# Initial sync of all .py files
Get-ChildItem "$source\*.py" | ForEach-Object {
    Sync-File $_.FullName
}
Write-Host "Initial sync complete. Watching for changes...`n" -ForegroundColor Green

# Set up FileSystemWatcher
$watcher = New-Object System.IO.FileSystemWatcher $source, "*.py"
$watcher.NotifyFilter = [System.IO.NotifyFilters]::LastWrite -bor [System.IO.NotifyFilters]::FileName
$watcher.IncludeSubdirectories = $false
$watcher.EnableRaisingEvents = $true

while ($true) {
    $result = $watcher.WaitForChanged([System.IO.WatcherChangeTypes]::All, 1000)
    if (-not $result.TimedOut) {
        $changed = Join-Path $source $result.Name
        if ($changed -match '\.py$') {
            Sync-File $changed
        }
    }
}
