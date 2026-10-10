# DiagTrack setup, first stage (Windows). Started by Setup.cmd.
#   1. Asks Windows for administrator rights.
#   2. Installs DiagTrack's own copy of Python 3.13 (official installer, signature checked) if it is not there.
#   3. Installs DiagTrack's Python packages.
#   4. Hands over to the setup program (installer\setup.py), which asks the questions and does the rest.
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

# ---- 1. Administrator rights ---------------------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'DiagTrack setup needs administrator rights. Windows will ask you to allow it.'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") + $Rest
    try {
        $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait -PassThru
        exit $p.ExitCode
    } catch {
        Write-Host 'Setup was not given administrator rights, so nothing was changed.' -ForegroundColor Red
        Read-Host 'Press Enter to close'
        exit 1
    }
}

$Data = Join-Path $env:ProgramData 'DiagTrack'
New-Item -ItemType Directory -Force -Path $Data | Out-Null
$Log = Join-Path $Data 'setup-bootstrap.log'
function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color; Add-Content -Path $Log -Value "$(Get-Date -Format s) $text" }
function Fail($text, $todo) {
    Say "Setup stopped: $text" 'Red'
    if ($todo) { Say "What to do: $todo" 'Yellow' }
    Say "Log file: $Log"
    if (-not $env:DT_UNATTENDED) { Read-Host 'Press Enter to close' }
    exit 1
}

Say ''
Say '================================================================' 'Cyan'
Say '  DiagTrack setup for Windows' 'White'
Say '  Windows 11, Windows Server 2016, 2019, 2022 and 2025' 'Gray'
Say '================================================================' 'Cyan'

# Files from a downloaded zip carry Windows' "from the internet" mark; remove it so the service wrapper can run.
Get-ChildItem -Path $Root -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

# Downloads on Windows Server 2016 need TLS 1.2 switched on explicitly.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

# ---- Quick system check (the full check comes in the setup program) ---------------------------
if (-not [Environment]::Is64BitOperatingSystem) { Fail 'DiagTrack needs 64-bit Windows.' 'Use a 64-bit Windows Server or Windows 11.' }
$os = Get-CimInstance Win32_OperatingSystem
$build = [int]$os.BuildNumber
if ($os.ProductType -eq 1 -and $build -lt 22000) { Fail "$($os.Caption) is not supported." 'Use Windows 11, or Windows Server 2016 or newer.' }
if ($os.ProductType -ne 1 -and $build -lt 14393) { Fail "$($os.Caption) is too old." 'Use Windows Server 2016 or newer.' }
Say "System: $($os.Caption) (build $build)"

# ---- 2. Python ------------------------------------------------------------------------------
$versions = Get-Content -Raw -Path (Join-Path $PSScriptRoot '..\versions.json') | ConvertFrom-Json
$PyDir = Join-Path $env:ProgramFiles 'DiagTrack\python'
$Py = Join-Path $PyDir 'python.exe'
$wanted = $versions.python.version
$have = ''
if (Test-Path $Py) { $have = (& $Py -c "import platform; print(platform.python_version())" 2>$null) }
if ($have -ne $wanted) {
    Say ''
    Say "Installing Python $wanted for DiagTrack (about 2 minutes)..." 'White'
    $exe = Join-Path $env:TEMP "python-$wanted-amd64.exe"
    try {
        Invoke-WebRequest -Uri $versions.python.url -OutFile $exe -UseBasicParsing
    } catch {
        Fail "Downloading Python failed: $($_.Exception.Message)" 'Check the internet connection, or allow www.python.org through the proxy.'
    }
    $sig = Get-AuthenticodeSignature -FilePath $exe
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike "*$($versions.python.signer)*") {
        Remove-Item $exe -Force -ErrorAction SilentlyContinue
        Fail "The Python download is not signed by $($versions.python.signer) (status $($sig.Status))." 'Run setup again. If it repeats, tell your IT team.'
    }
    $pyArgs = @('/quiet', 'InstallAllUsers=1', "TargetDir=`"$PyDir`"", 'PrependPath=0', 'Include_launcher=0',
                'InstallLauncherAllUsers=0', 'Include_test=0', 'Include_doc=0', 'Include_tcltk=0', 'Shortcuts=0',
                'AssociateFiles=0', 'CompileAll=0', '/log', "`"$(Join-Path $Data 'python-install.log')`"")
    $p = Start-Process -FilePath $exe -ArgumentList $pyArgs -Wait -PassThru
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    if (($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) -or -not (Test-Path $Py)) {
        Fail "Installing Python failed (code $($p.ExitCode))." "See $(Join-Path $Data 'python-install.log')."
    }
    Say "Python $wanted installed in $PyDir." 'Green'
} else {
    Say "Python $wanted is already installed for DiagTrack."
}

# ---- 3. Python packages --------------------------------------------------------------------
Say ''
Say 'Installing DiagTrack''s Python packages (about 1 minute)...' 'White'
& $Py -m pip install --disable-pip-version-check --no-warn-script-location -q -r (Join-Path $Root 'app\requirements.txt') 2>&1 | Add-Content -Path $Log
if ($LASTEXITCODE -ne 0) { Fail 'Installing the Python packages failed.' 'Allow pypi.org and files.pythonhosted.org through the proxy, then run setup again.' }
Say 'Packages installed.' 'Green'

# ---- 4. Hand over ---------------------------------------------------------------------------
& $Py (Join-Path $Root 'installer\setup.py') --windows --source $Root @Rest
exit $LASTEXITCODE
