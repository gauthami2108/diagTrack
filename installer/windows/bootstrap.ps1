# DiagTrack setup, first stage (Windows). Started by Setup.cmd.
#   1. Asks Windows for administrator rights.
#   2. Installs DiagTrack's own copy of Python 3.13 (official installer, signature checked) if it is not there.
#   3. Installs DiagTrack's Python packages.
#   4. Hands over to the setup program (installer\setup.py), which asks the questions and does the rest.
# Arguments for setup (for example --answers file.json install) arrive in $args and are passed on unchanged.
$Rest = @($args)
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
if (Test-Path $Py) { try { $have = (& $Py -c "import platform; print(platform.python_version())") } catch { $have = '' } }
if ($have -ne $wanted) {
    Say ''
    Say "Installing Python $wanted for DiagTrack (about 2 minutes)..." 'White'
    # The NuGet build of Python is a self-contained folder: no registry entries, no effect on any other Python here.
    $zip = Join-Path $env:TEMP "python-$wanted.zip"
    $unpack = Join-Path $env:TEMP "python-$wanted"
    try {
        Invoke-WebRequest -Uri $versions.python.url -OutFile $zip -UseBasicParsing
    } catch {
        Fail "Downloading Python failed: $($_.Exception.Message)" 'Check the internet connection, or allow www.nuget.org and globalcdn.nuget.org through the proxy.'
    }
    if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $unpack -Force
    $sig = Get-AuthenticodeSignature -FilePath (Join-Path $unpack 'tools\python.exe')
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike "*$($versions.python.signer)*") {
        Fail "The downloaded Python is not signed by $($versions.python.signer) (status $($sig.Status))." 'Run setup again. If it repeats, tell your IT team.'
    }
    if (Test-Path $PyDir) { Remove-Item $PyDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Copy-Item -Path (Join-Path $unpack 'tools\*') -Destination $PyDir -Recurse -Force
    Remove-Item $zip, $unpack -Recurse -Force -ErrorAction SilentlyContinue
    $ErrorActionPreference = 'Continue'
    # A clean Windows Server 2016 may lack the Microsoft Visual C++ runtime that Python needs.
    & $Py -c "print(1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        Say 'Installing the Microsoft Visual C++ runtime that Python needs...'
        $vc = Join-Path $env:TEMP 'vc_redist.x64.exe'
        Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vc -UseBasicParsing
        $vsig = Get-AuthenticodeSignature -FilePath $vc
        if ($vsig.Status -ne 'Valid' -or $vsig.SignerCertificate.Subject -notlike '*Microsoft Corporation*') {
            Fail 'The Visual C++ runtime download is not signed by Microsoft.' 'Run setup again.'
        }
        Start-Process -FilePath $vc -ArgumentList '/install', '/quiet', '/norestart' -Wait | Out-Null
        Remove-Item $vc -Force -ErrorAction SilentlyContinue
        & $Py -c "print(1)" *> $null
        if ($LASTEXITCODE -ne 0) { Fail 'Python does not start on this computer.' 'Install the latest Windows updates, restart, and run setup again.' }
    }
    & $Py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { & $Py -m ensurepip --default-pip 2>&1 | Add-Content -Path $Log }
    $ErrorActionPreference = 'Stop'
    if (-not (Test-Path $Py)) { Fail 'Installing Python failed.' "See $Log." }
    Say "Python $wanted installed in $PyDir." 'Green'
} else {
    Say "Python $wanted is already installed for DiagTrack."
}

# ---- 3. Python packages --------------------------------------------------------------------
Say ''
Say 'Installing DiagTrack''s Python packages (about 1 minute)...' 'White'
$ErrorActionPreference = 'Continue'  # pip writes notes to the error stream; only its exit code matters
& $Py -m pip install --disable-pip-version-check --no-warn-script-location -q -r (Join-Path $Root 'app\requirements.txt') 2>&1 | Add-Content -Path $Log
if ($LASTEXITCODE -ne 0) { Fail 'Installing the Python packages failed.' 'Allow pypi.org and files.pythonhosted.org through the proxy, then run setup again.' }
Say 'Packages installed.' 'Green'

# ---- 4. Hand over ---------------------------------------------------------------------------
& $Py (Join-Path $Root 'installer\setup.py') --windows --source $Root @Rest
exit $LASTEXITCODE
