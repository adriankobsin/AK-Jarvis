<#
  Jarvis Chrome: lets Jarvis (on the server) control a Chrome window on this Windows computer.

  First time only:   Jarvis-Chrome.cmd -Setup
      Creates a tunnel-only SSH key, installs it on the server (asks for the ROOT password once)
      and puts a "Jarvis Chrome" shortcut on your desktop.

  Every time:        double-click "Jarvis Chrome" on the desktop
      Opens a separate Chrome window ("Jarvis profile") and connects it to Jarvis through an
      encrypted SSH tunnel over Tailscale. Close that Chrome window to disconnect Jarvis.

  The Jarvis profile is separate from your normal Chrome: Jarvis only sees the sites you
  log in to inside that window. The key can only open this one tunnel, nothing else.
#>
param(
  [switch]$Setup,
  [string]$Server = "ubuntu-4gb-hel1-4.tail411b86.ts.net",
  [int]$Port = 9222
)
$ErrorActionPreference = "Stop"
$KeyFile = Join-Path $HOME ".ssh\jarvis_chrome"
$JarvisProfile = Join-Path $env:LOCALAPPDATA "JarvisChrome"

function Find-Chrome {
  $c = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
         "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
         "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $c) { throw "Google Chrome not found. Install it from google.com/chrome and run again." }
  $c
}
function Test-Debug {
  try { (Invoke-WebRequest "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false }
}

if ($Setup) {
  Write-Host "`n== Jarvis Chrome setup ==" -ForegroundColor Cyan
  if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "OpenSSH client missing: Settings > System > Optional features > Add 'OpenSSH Client', then run again."
  }
  New-Item -ItemType Directory -Force (Split-Path $KeyFile) | Out-Null
  if (-not (Test-Path $KeyFile)) {
    # via cmd so the empty passphrase survives PowerShell's argument quoting
    cmd /c "ssh-keygen -q -t ed25519 -N `"`" -C jarvis-chrome -f `"$KeyFile`""
    if (-not (Test-Path $KeyFile)) { throw "Couldn't create the SSH key." }
  }
  $pub = (Get-Content "$KeyFile.pub" -Raw).Trim()
  # Tunnel-only key: no shell, no other forwarding, may only open port $Port on the server's localhost.
  $line = "restrict,port-forwarding,permitlisten=`"127.0.0.1:$Port`",command=`"echo tunnel-only`" $pub"
  Write-Host "Installing the key on the server. Enter the ROOT password when asked." -ForegroundColor Yellow
  # Sent base64-encoded: Windows PowerShell mangles double quotes in arguments to ssh.exe
  $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($line))
  $remote = "install -d -m 700 -o jarvis -g jarvis /home/jarvis/.ssh && " +
            "touch /home/jarvis/.ssh/authorized_keys && " +
            "(grep -qF '$($pub.Split(' ')[1])' /home/jarvis/.ssh/authorized_keys || echo $b64 | base64 -d >> /home/jarvis/.ssh/authorized_keys; echo >> /home/jarvis/.ssh/authorized_keys) && " +
            "chown jarvis:jarvis /home/jarvis/.ssh/authorized_keys && chmod 600 /home/jarvis/.ssh/authorized_keys && echo KEY-OK"
  $out = ssh -o StrictHostKeyChecking=accept-new "root@$Server" $remote
  if (-not (($out -join "`n") -match "KEY-OK")) { throw "Couldn't install the key on the server (is Tailscale on? is the root password right?)." }
  Write-Host "  key installed" -ForegroundColor Green

  $cmd = Join-Path $PSScriptRoot "Jarvis-Chrome.cmd"
  $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Jarvis Chrome.lnk"
  $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $sh.TargetPath = $cmd; $sh.WorkingDirectory = $PSScriptRoot; $sh.WindowStyle = 7
  $sh.IconLocation = "$(Find-Chrome),0"; $sh.Save()
  Write-Host "  desktop shortcut 'Jarvis Chrome' created" -ForegroundColor Green
  Write-Host "`nDone. Double-click 'Jarvis Chrome' on your desktop to connect Jarvis.`n" -ForegroundColor Cyan
  exit 0
}

if (-not (Test-Path $KeyFile)) { throw "Run setup first:  Jarvis-Chrome.cmd -Setup" }

# 1) Chrome with remote debugging, in its own "Jarvis" profile (required by Chrome 136+).
if (-not (Test-Debug)) {
  Start-Process (Find-Chrome) -ArgumentList "--remote-debugging-port=$Port", "--user-data-dir=`"$JarvisProfile`"",
    "--no-first-run", "--no-default-browser-check", "https://www.google.com"
  for ($i = 0; $i -lt 30 -and -not (Test-Debug); $i++) { Start-Sleep -Milliseconds 500 }
  if (-not (Test-Debug)) { throw "Chrome didn't open its debug port. Close all 'Jarvis Chrome' windows and try again." }
}
Write-Host "Jarvis Chrome is open. Connecting to Jarvis... (close the Jarvis Chrome window to disconnect)" -ForegroundColor Cyan

# 2) Keep the tunnel up while that Chrome is open: server 127.0.0.1:$Port -> this computer 127.0.0.1:$Port
$sshArgs = @("-i", $KeyFile, "-N", "-T", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
             "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
             "-R", "127.0.0.1:${Port}:127.0.0.1:$Port", "jarvis@$Server")
$ssh = $null
try {
  while (Test-Debug) {
    if (-not $ssh -or $ssh.HasExited) {
      if ($ssh) { Write-Host "Tunnel dropped, reconnecting..." -ForegroundColor Yellow; Start-Sleep 5 }
      $ssh = Start-Process ssh -ArgumentList $sshArgs -PassThru -WindowStyle Hidden
      Start-Sleep 3
      if (-not $ssh.HasExited) { Write-Host "Connected. Jarvis can now use this Chrome window." -ForegroundColor Green }
    }
    Start-Sleep 5
  }
} finally {
  if ($ssh -and -not $ssh.HasExited) { Stop-Process -Id $ssh.Id -Force }
  Write-Host "Jarvis Chrome closed. Jarvis is back on his own browser." -ForegroundColor Cyan
}
