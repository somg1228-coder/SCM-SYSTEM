param(
    [string]$RepoPath = $PSScriptRoot,
    [string]$RemoteName = "origin",
    [string]$RemoteUrl = "https://github.com/somg1228-coder/SCM-SYSTEM.git",
    [string]$Branch = "main",
    [int]$DebounceSeconds = 5,
    [int]$RetryCount = 3,
    [int]$RetryDelaySeconds = 20,
    [string]$LogFilePath = ""
)

$ErrorActionPreference = "Stop"

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$LogFile = if ($LogFilePath) { $LogFilePath } else { Join-Path $RepoPath "auto_git_push.log" }
$PidFile = Join-Path $RepoPath "auto_git_push.pid"

function Write-Log {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogFile -Value "[$timestamp] $Message"
}

function Write-Status {
    param([string]$Message)

    Write-Host "[auto-git-push] $Message"
    Write-Log $Message
}

function ConvertTo-ProcessArgument {
    param([string]$Argument)

    if ($null -eq $Argument) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }
    return '"' + ($Argument -replace '"', '\"') + '"'
}

function Join-ProcessArguments {
    param([string[]]$Arguments)

    return (($Arguments | ForEach-Object { ConvertTo-ProcessArgument -Argument $_ }) -join " ")
}

function Invoke-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$GitArgs,
        [switch]$AllowFailure
    )

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = "git"
    $process.StartInfo.WorkingDirectory = $RepoPath
    $process.StartInfo.Arguments = Join-ProcessArguments -Arguments $GitArgs
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.CreateNoWindow = $true

    try {
        $started = $process.Start()
        if (-not $started) {
            throw "git process did not start."
        }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $output = @()
        if ($stdout) {
            $output += @($stdout -split "`r?`n" | Where-Object { $_ -ne "" })
        }
        if ($stderr) {
            $output += @($stderr -split "`r?`n" | Where-Object { $_ -ne "" })
        }
    }
    finally {
        $process.Dispose()
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $details = ($output | Out-String).Trim()
        throw "git $($GitArgs -join ' ') failed with exit code $exitCode. $details"
    }

    return @{
        ExitCode = $exitCode
        Output = @($output)
    }
}

function Test-RepositoryReady {
    Invoke-GitOutput -GitArgs @("rev-parse", "--is-inside-work-tree") | Out-Null

    $currentBranch = (Invoke-GitOutput -GitArgs @("branch", "--show-current")).Output | Select-Object -First 1
    if ($currentBranch -ne $Branch) {
        Write-Status "Skipped: current branch is '$currentBranch', expected '$Branch'."
        return $false
    }

    $remoteResult = Invoke-GitOutput -GitArgs @("remote", "get-url", $RemoteName) -AllowFailure
    if ($remoteResult.ExitCode -ne 0) {
        Invoke-GitOutput -GitArgs @("remote", "add", $RemoteName, $RemoteUrl) | Out-Null
        Write-Status "Added remote '$RemoteName'."
    }
    else {
        $currentRemoteUrl = $remoteResult.Output | Select-Object -First 1
        if ($currentRemoteUrl -ne $RemoteUrl) {
            Invoke-GitOutput -GitArgs @("remote", "set-url", $RemoteName, $RemoteUrl) | Out-Null
            Write-Status "Updated remote '$RemoteName' URL."
        }
    }

    return $true
}

function Test-HasWorkingTreeChanges {
    return ((Get-AutoPushCandidatePaths).Count -gt 0)
}

function Test-HasUnpushedCommits {
    $result = Invoke-GitOutput -GitArgs @("rev-list", "--count", "$RemoteName/$Branch..HEAD") -AllowFailure
    if ($result.ExitCode -ne 0) {
        return $true
    }

    $countText = $result.Output | Select-Object -First 1
    $count = 0
    if ([int]::TryParse($countText, [ref]$count)) {
        return ($count -gt 0)
    }

    return $true
}

function Test-IgnoredPath {
    param([string]$Path)

    if (-not $Path) {
        return $false
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($Path)
    }
    catch {
        return $false
    }

    if (-not $fullPath.StartsWith($RepoPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    $relativePath = $fullPath.Substring($RepoPath.Length).TrimStart("\", "/")
    $normalized = $relativePath -replace "\\", "/"
    $parts = $normalized -split "/"

    $ignoredDirectories = @(".git", "__pycache__", ".venv", "venv", "env", "node_modules")
    foreach ($part in $parts) {
        if ($ignoredDirectories -contains $part) {
            return $true
        }
    }

    $extension = [System.IO.Path]::GetExtension($normalized).ToLowerInvariant()
    $ignoredExtensions = @(".log", ".pid", ".db", ".db-journal", ".db-wal", ".db-shm", ".pyc", ".pem", ".key")
    if ($ignoredExtensions -contains $extension) {
        return $true
    }

    $ignoredFiles = @(".env", ".streamlit/secrets.toml")
    if ($ignoredFiles -contains $normalized) {
        return $true
    }

    return (
        ($normalized -like ".env.*" -and $normalized -ne ".env.example") -or
        ($normalized -like ".streamlit/secrets.*.toml" -and $normalized -ne ".streamlit/secrets.example.toml") -or
        ($normalized -like "data/streamlit.*.log") -or
        ($normalized -like "ReturnCaseSystem/streamlit.*.log") -or
        ($normalized -like "data/warehouse3d_layout_backups/*")
    )
}

function Convert-GitStatusLineToPaths {
    param([string]$Line)

    if ([string]::IsNullOrWhiteSpace($Line) -or $Line.Length -lt 4) {
        return @()
    }

    $pathText = $Line.Substring(3).Trim()
    if ($pathText -match " -> ") {
        return @($pathText -split " -> ", 2 | ForEach-Object { $_.Trim('"') })
    }

    return @($pathText.Trim('"'))
}

function Get-AutoPushCandidatePaths {
    $status = (Invoke-GitOutput -GitArgs @("status", "--porcelain", "--untracked-files=normal")).Output
    $paths = New-Object System.Collections.Generic.List[string]

    foreach ($line in $status) {
        foreach ($relativePath in (Convert-GitStatusLineToPaths -Line $line)) {
            if (-not $relativePath) {
                continue
            }
            $normalizedRelative = $relativePath -replace "/", "\"
            $fullPath = Join-Path $RepoPath $normalizedRelative
            if (-not (Test-IgnoredPath -Path $fullPath)) {
                $paths.Add($normalizedRelative)
            }
        }
    }

    return @($paths | Select-Object -Unique)
}

function Invoke-RebaseRecovery {
    $rebaseMergePath = (Invoke-GitOutput -GitArgs @("rev-parse", "--git-path", "rebase-merge")).Output | Select-Object -First 1
    $rebaseApplyPath = (Invoke-GitOutput -GitArgs @("rev-parse", "--git-path", "rebase-apply")).Output | Select-Object -First 1
    $wasRebasing = (Test-Path -LiteralPath $rebaseMergePath) -or (Test-Path -LiteralPath $rebaseApplyPath)

    try {
        Invoke-GitOutput -GitArgs @("fetch", $RemoteName, $Branch) | Out-Null
        Invoke-GitOutput -GitArgs @("pull", "--rebase", "--autostash", $RemoteName, $Branch) | Out-Null
        Write-Status "Rebased with $RemoteName/$Branch after push failure."
    }
    catch {
        Write-Status "Rebase recovery failed: $($_.Exception.Message)"

        $isRebasing = (Test-Path -LiteralPath $rebaseMergePath) -or (Test-Path -LiteralPath $rebaseApplyPath)
        if (-not $wasRebasing -and $isRebasing) {
            Invoke-GitOutput -GitArgs @("rebase", "--abort") -AllowFailure | Out-Null
            Write-Status "Aborted failed automatic rebase."
        }
    }
}

function Invoke-AutoPush {
    if (-not (Test-RepositoryReady)) {
        return $false
    }

    if (-not (Test-HasWorkingTreeChanges) -and -not (Test-HasUnpushedCommits)) {
        return $true
    }

    for ($attempt = 1; $attempt -le $RetryCount; $attempt++) {
        try {
            $candidatePaths = Get-AutoPushCandidatePaths
            if ($candidatePaths.Count -gt 0) {
                Write-Status "Detected $($candidatePaths.Count) changed file(s): $($candidatePaths -join ', ')"
                Invoke-GitOutput -GitArgs (@("add", "--") + @($candidatePaths)) | Out-Null
                Write-Status "git add completed."

                $staged = (Invoke-GitOutput -GitArgs @("diff", "--cached", "--name-only")).Output
                if ($staged.Count -gt 0) {
                    $message = "Auto Update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
                    Invoke-GitOutput -GitArgs @("commit", "-m", $message) | Out-Null
                    Write-Status "Committed: $message"
                }
                else {
                    Write-Status "No staged changes. Nothing to commit."
                }
            }

            if (Test-HasUnpushedCommits) {
                Invoke-GitOutput -GitArgs @("push", $RemoteName, $Branch) | Out-Null
                Write-Status "Pushed to $RemoteName/$Branch."
            }

            return $true
        }
        catch {
            Write-Status "Attempt $attempt failed: $($_.Exception.Message)"

            if ($attempt -lt $RetryCount) {
                Invoke-RebaseRecovery
                Start-Sleep -Seconds $RetryDelaySeconds
            }
        }
    }

    Write-Status "Auto push failed after $RetryCount attempts. It will retry after the next debounce window."
    return $false
}

function New-MutexName {
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($RepoPath.ToLowerInvariant())
        $hashBytes = $sha1.ComputeHash($bytes)
        $hash = -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
        return "Global\SCM_PORTAL_AUTO_GIT_PUSH_$hash"
    }
    finally {
        $sha1.Dispose()
    }
}

$mutex = New-Object System.Threading.Mutex($false, (New-MutexName))
if (-not $mutex.WaitOne(0)) {
    Write-Status "Another auto git push watcher is already running. Exiting."
    exit 0
}

$watcher = $null
$registrations = @()

try {
    Set-Content -LiteralPath $PidFile -Value $PID -Encoding ascii
    Write-Status "Auto git push watcher running. PID=$PID Repo=$RepoPath Log=$LogFile"

    $watcher = New-Object System.IO.FileSystemWatcher
    $watcher.Path = $RepoPath
    $watcher.IncludeSubdirectories = $true
    $watcher.NotifyFilter = [System.IO.NotifyFilters]"FileName, DirectoryName, LastWrite, Size"
    $watcher.EnableRaisingEvents = $true

    foreach ($eventName in @("Changed", "Created", "Deleted", "Renamed")) {
        $registrations += Register-ObjectEvent -InputObject $watcher -EventName $eventName -SourceIdentifier "AutoGitPush.$eventName"
    }

    $pending = $false
    $nextRunAt = [DateTime]::MaxValue

    if ((Test-RepositoryReady) -and ((Test-HasWorkingTreeChanges) -or (Test-HasUnpushedCommits))) {
        $pending = $true
        $nextRunAt = (Get-Date).AddSeconds($DebounceSeconds)
        Write-Log "Startup sync scheduled."
    }

    while ($true) {
        $timeout = 5
        if ($pending) {
            $secondsUntilRun = [Math]::Ceiling(($nextRunAt - (Get-Date)).TotalSeconds)
            $timeout = [Math]::Max(1, [Math]::Min(5, $secondsUntilRun))
        }

        $receivedEvent = Wait-Event -Timeout $timeout
        if ($receivedEvent) {
            $events = @($receivedEvent) + @(Get-Event | Where-Object { $_.SourceIdentifier -like "AutoGitPush.*" })
            foreach ($queuedEvent in $events) {
                Remove-Event -EventIdentifier $queuedEvent.EventIdentifier -ErrorAction SilentlyContinue

                $path = $queuedEvent.SourceEventArgs.FullPath
                if (-not (Test-IgnoredPath -Path $path)) {
                    Write-Status "File change detected: $path"
                    $pending = $true
                    $nextRunAt = (Get-Date).AddSeconds($DebounceSeconds)
                }
            }
        }

        if ($pending -and (Get-Date) -ge $nextRunAt) {
            $completed = Invoke-AutoPush
            $pending = -not $completed
            if ($pending) {
                $nextRunAt = (Get-Date).AddSeconds($RetryDelaySeconds)
            }
        }
    }
}
catch {
    Write-Status "Fatal watcher error: $($_.Exception.Message)"
    throw
}
finally {
    foreach ($registration in $registrations) {
        Unregister-Event -SubscriptionId $registration.Id -ErrorAction SilentlyContinue
    }

    if ($watcher) {
        $watcher.Dispose()
    }

    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Log "Auto git push watcher stopped. PID=$PID"
    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}
