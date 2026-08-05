param(
    [string]$RepoPath = $PSScriptRoot,
    [string]$RemoteName = "origin",
    [string]$RemoteUrl = "https://github.com/somg1228-coder/SCM-SYSTEM.git",
    [string]$Branch = "main",
    [int]$DebounceSeconds = 5,
    [int]$RetryCount = 3,
    [int]$RetryDelaySeconds = 20
)

$ErrorActionPreference = "Stop"

$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$LogFile = Join-Path $RepoPath ".git\auto_git_push.log"

function Write-Log {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogFile -Value "[$timestamp] $Message"
}

function Invoke-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$GitArgs,
        [switch]$AllowFailure
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & git -C $RepoPath @GitArgs 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
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
        Write-Log "Skipped: current branch is '$currentBranch', expected '$Branch'."
        return $false
    }

    $remoteResult = Invoke-GitOutput -GitArgs @("remote", "get-url", $RemoteName) -AllowFailure
    if ($remoteResult.ExitCode -ne 0) {
        Invoke-GitOutput -GitArgs @("remote", "add", $RemoteName, $RemoteUrl) | Out-Null
        Write-Log "Added remote '$RemoteName'."
    }
    else {
        $currentRemoteUrl = $remoteResult.Output | Select-Object -First 1
        if ($currentRemoteUrl -ne $RemoteUrl) {
            Invoke-GitOutput -GitArgs @("remote", "set-url", $RemoteName, $RemoteUrl) | Out-Null
            Write-Log "Updated remote '$RemoteName' URL."
        }
    }

    return $true
}

function Test-HasWorkingTreeChanges {
    $status = (Invoke-GitOutput -GitArgs @("status", "--porcelain", "--untracked-files=normal")).Output
    return ($status.Count -gt 0)
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

    $ignoredFiles = @(".env", ".streamlit/secrets.toml")
    return ($ignoredFiles -contains $normalized)
}

function Invoke-RebaseRecovery {
    $rebaseMergePath = (Invoke-GitOutput -GitArgs @("rev-parse", "--git-path", "rebase-merge")).Output | Select-Object -First 1
    $rebaseApplyPath = (Invoke-GitOutput -GitArgs @("rev-parse", "--git-path", "rebase-apply")).Output | Select-Object -First 1
    $wasRebasing = (Test-Path -LiteralPath $rebaseMergePath) -or (Test-Path -LiteralPath $rebaseApplyPath)

    try {
        Invoke-GitOutput -GitArgs @("fetch", $RemoteName, $Branch) | Out-Null
        Invoke-GitOutput -GitArgs @("pull", "--rebase", "--autostash", $RemoteName, $Branch) | Out-Null
        Write-Log "Rebased with $RemoteName/$Branch after push failure."
    }
    catch {
        Write-Log "Rebase recovery failed: $($_.Exception.Message)"

        $isRebasing = (Test-Path -LiteralPath $rebaseMergePath) -or (Test-Path -LiteralPath $rebaseApplyPath)
        if (-not $wasRebasing -and $isRebasing) {
            Invoke-GitOutput -GitArgs @("rebase", "--abort") -AllowFailure | Out-Null
            Write-Log "Aborted failed automatic rebase."
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
            if (Test-HasWorkingTreeChanges) {
                Invoke-GitOutput -GitArgs @("add", ".") | Out-Null

                $staged = (Invoke-GitOutput -GitArgs @("diff", "--cached", "--name-only")).Output
                if ($staged.Count -gt 0) {
                    $message = "Auto Update $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
                    Invoke-GitOutput -GitArgs @("commit", "-m", $message) | Out-Null
                    Write-Log "Committed: $message"
                }
                else {
                    Write-Log "No staged changes. Nothing to commit."
                }
            }

            if (Test-HasUnpushedCommits) {
                Invoke-GitOutput -GitArgs @("push", $RemoteName, $Branch) | Out-Null
                Write-Log "Pushed to $RemoteName/$Branch."
            }

            return $true
        }
        catch {
            Write-Log "Attempt $attempt failed: $($_.Exception.Message)"

            if ($attempt -lt $RetryCount) {
                Invoke-RebaseRecovery
                Start-Sleep -Seconds $RetryDelaySeconds
            }
        }
    }

    Write-Log "Auto push failed after $RetryCount attempts. It will retry after the next debounce window."
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
    Write-Log "Another auto git push watcher is already running. Exiting."
    exit 0
}

$watcher = $null
$registrations = @()

try {
    Write-Log "Auto git push watcher started for $RepoPath."

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
finally {
    foreach ($registration in $registrations) {
        Unregister-Event -SubscriptionId $registration.Id -ErrorAction SilentlyContinue
    }

    if ($watcher) {
        $watcher.Dispose()
    }

    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}
