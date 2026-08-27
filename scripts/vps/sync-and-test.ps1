param(
  [string]$HostName = "root@185.99.135.224",
  [string]$RepoUrl = "https://github.com/FengYuchen1314/open-node.git",
  [string]$Branch = "main",
  [string]$RemoteDir = "/opt/open-node",
  [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"

if ($RemoteDir -notmatch "^/opt/open-node(/[A-Za-z0-9][A-Za-z0-9._-]*)?$") {
  throw "RemoteDir must be /opt/open-node or a direct non-hidden child."
}
if ($HostName -notmatch "^[A-Za-z0-9][A-Za-z0-9_.@:-]*$") {
  throw "Invalid SSH host name."
}
git check-ref-format "refs/heads/$Branch"
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
$revision = git rev-parse --verify "refs/heads/$Branch^{commit}"
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
git push origin "refs/heads/${Branch}:refs/heads/${Branch}"
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

# Structured input avoids interpolating repository/branch values into a remote shell.
$options = @{
  remote_dir = $RemoteDir
  repo_url = $RepoUrl
  branch = $Branch
  revision = $revision.Trim()
  skip_bootstrap = $SkipBootstrap.IsPresent
} | ConvertTo-Json -Compress
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($options))
Get-Content -Raw -LiteralPath "$PSScriptRoot/sync-and-test.py" |
  ssh -o BatchMode=yes -o ServerAliveInterval=15 $HostName "python3 - $encoded"
$sshExitCode = $LASTEXITCODE
if ($sshExitCode -ne 0) {
  exit $sshExitCode
}
