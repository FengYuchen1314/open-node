param(
  [string]$HostName = "root@185.99.135.224",
  [string]$RepoUrl = "https://github.com/FengYuchen1314/open-node.git",
  [string]$Branch = "main",
  [string]$RemoteDir = "/opt/open-node",
  [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"

if ($RemoteDir -notmatch "^/opt/open-node(/[-A-Za-z0-9._]+)?$") {
  throw "RemoteDir must be /opt/open-node or a direct child of /opt/open-node."
}

git push -u origin $Branch
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

$remoteCommand = @"
set -euo pipefail
REMOTE_DIR='$RemoteDir'
REPO_URL='$RepoUrl'
BRANCH='$Branch'

case "`$REMOTE_DIR" in
  /opt/open-node|/opt/open-node/*) ;;
  *) echo "Refusing unsafe remote directory: `$REMOTE_DIR" >&2; exit 64 ;;
esac

if [ ! -d "`$REMOTE_DIR/.git" ]; then
  rm -rf -- "`$REMOTE_DIR"
  git clone --branch "`$BRANCH" "`$REPO_URL" "`$REMOTE_DIR"
else
  git -C "`$REMOTE_DIR" fetch origin "`$BRANCH"
  git -C "`$REMOTE_DIR" reset --hard "origin/`$BRANCH"
fi

if [ "$SkipBootstrap" != "True" ]; then
  bash "`$REMOTE_DIR/scripts/vps/bootstrap-debian.sh"
fi

bash "`$REMOTE_DIR/scripts/vps/run-tests.sh"
"@

ssh $HostName $remoteCommand
$sshExitCode = $LASTEXITCODE
if ($sshExitCode -ne 0) {
  exit $sshExitCode
}
