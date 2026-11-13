<#
.SYNOPSIS
Install, upgrade or remove a blt remote collector as a Windows service.

.DESCRIPTION
Run as Administrator, from the unpacked blt-collector-windows-<version>
folder (docs/windows-collector.md):

  .\install.ps1 -Name uk -Url https://blt.example.com -TrustCertificate
  .\install.ps1 -Name uk                    # upgrade: the new bundle's install.ps1, same settings
  .\install.ps1 -Name uk -Reconfigure       # ask for the password and API key again
  .\install.ps1 -Name uk -Uninstall

It installs blt and its own Python to C:\Program Files\blt-collector\<version>,
keeps the collector's settings in C:\ProgramData\blt-collector\<name>
(readable by SYSTEM and Administrators only), and registers the service
blt-collector-<name>: started at boot, started again if it ends with an
error, its logs in C:\ProgramData\blt-collector\<name>\logs.

Prefect's password and blt's API key are asked for, never taken from the
command line, and never shown or logged. For an unattended install they
may come from the environment instead: BLT_COLLECTOR_PREFECT_AUTH
(user:password) and BLT_COLLECTOR_API_KEY.

.PARAMETER Name
The collector's name, as sources name it in Administration -> Sources:
lower case letters, digits and dashes. May be left out when this server
has exactly one collector.

.PARAMETER Url
blt's address, https://<server>, with no path. Prefect is reached at
<Url>/prefect/api and blt's API at <Url>/api, both on that port. Needed
the first time; an upgrade keeps it.

.PARAMETER TrustCertificate
Fetch blt's certificate from the server, show its SHA-256 fingerprint,
and trust it (for this collector only) once you confirm it is the one
deploy/up.sh printed. For blt's self-signed certificate.

.PARAMETER CertificateFingerprint
With -TrustCertificate: the SHA-256 fingerprint to expect, as up.sh or
openssl prints it. Trusted without asking if it matches; refused if not.

.PARAMETER CertificateFile
A PEM or DER file to trust instead: blt's certificate, or the CA that
signed it. For a certificate from your own CA.

.PARAMETER Reconfigure
Ask for Prefect's password and blt's API key again (an upgrade keeps them).

.PARAMETER WorkPool
Prefect's work pool (blt). Only for a blt that set BLT_WORK_POOL.

.PARAMETER SkipCheck
Do not see whether Prefect and the API answer before starting the service.

.PARAMETER Uninstall
Stop and remove the service, its settings and logs; and blt from Program
Files when no other collector uses it.
#>
[CmdletBinding()]
param(
    [string]$Name,
    [string]$Url,
    [switch]$TrustCertificate,
    [string]$CertificateFingerprint,
    [string]$CertificateFile,
    [switch]$Reconfigure,
    [string]$WorkPool,
    [switch]$SkipCheck,
    [switch]$Uninstall
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ProgramRoot = Join-Path $env:ProgramFiles 'blt-collector'
$DataRoot = Join-Path $env:ProgramData 'blt-collector'
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Fail([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Test-Administrator {
    $me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# SYSTEM (the service runs as it) and Administrators: nobody else may read
# what is in here. Nothing inherited from ProgramData, which lets every
# user read.
function Protect-Folder([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Path $Path | Out-Null }
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $who = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $who, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-Text([string]$Path, [string]$Text) {
    # UTF-8 without a BOM: OpenSSL reads the certificates, Python the rest.
    $part = "$Path.part"
    [System.IO.File]::WriteAllText($part, $Text, $Utf8)
    Move-Item -LiteralPath $part -Destination $Path -Force
}

function Read-Secret([string]$Prompt, [string]$FromEnvironment) {
    $value = [Environment]::GetEnvironmentVariable($FromEnvironment)
    if ($value) { return $value }
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-Fingerprint([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Certificate.RawData))).Replace('-', ':') }
    finally { $sha.Dispose() }
}

function ConvertTo-Pem([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate) {
    $body = [Convert]::ToBase64String($Certificate.RawData, 'InsertLineBreaks')
    return "-----BEGIN CERTIFICATE-----`n$($body.Replace("`r`n", "`n"))`n-----END CERTIFICATE-----`n"
}

# The certificate the server presents, to be shown and confirmed before
# it is trusted. Only this read accepts any certificate: what it returns
# is trusted only once its fingerprint is confirmed, and the collector
# itself always verifies.
function Get-ServerCertificate([string]$HostName, [int]$Port) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $tcp.Connect($HostName, $Port)
        $anyForNow = [System.Net.Security.RemoteCertificateValidationCallback] { return $true }
        $tls = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $anyForNow)
        # TLS 1.2 by name: Windows PowerShell 5.1 may otherwise offer only 1.0.
        $tls.AuthenticateAsClient($HostName, $null, [System.Security.Authentication.SslProtocols]::Tls12, $false)
        return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($tls.RemoteCertificate)
    }
    finally { $tcp.Close() }
}

function Get-ServiceWrapper([string]$Data, [string]$Id) {
    return Join-Path $Data "$Id.exe"
}

function Invoke-Wrapper([string]$Wrapper, [string]$Command) {
    & $Wrapper $Command
    if ($LASTEXITCODE -ne 0) { Fail "$Command of the service failed (exit $LASTEXITCODE)." }
}

function Stop-Collector([string]$Id) {
    $service = Get-Service -Name $Id -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') {
        Write-Host "Stopping $Id..."
        Stop-Service -Name $Id -Force -ErrorAction SilentlyContinue
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(90))
    }
    return $service
}

# Program Files\blt-collector\<version> folders no collector's service uses.
function Remove-UnusedVersions([string]$Keep) {
    if (-not (Test-Path -LiteralPath $ProgramRoot)) { return }
    $used = @()
    if (Test-Path -LiteralPath $DataRoot) {
        foreach ($xml in Get-ChildItem -Path $DataRoot -Filter 'blt-collector-*.xml' -Recurse -ErrorAction SilentlyContinue) {
            $used += (Get-Content -LiteralPath $xml.FullName -Raw)
        }
    }
    foreach ($folder in Get-ChildItem -LiteralPath $ProgramRoot -Directory) {
        if ($folder.FullName -eq $Keep) { continue }
        $inUse = $false
        foreach ($text in $used) { if ($text.Contains($folder.FullName + '\')) { $inUse = $true } }
        if (-not $inUse) {
            Write-Host "Removing $($folder.FullName), which no collector uses now."
            Remove-Item -LiteralPath $folder.FullName -Recurse -Force
        }
    }
}

if (-not (Test-Administrator)) { Fail 'Run this as Administrator.' }

# --- Which collector ------------------------------------------------------
if (-not $Name) {
    $known = @(Get-ChildItem -Path $DataRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'collector.json') })
    if ($known.Count -eq 1) { $Name = $known[0].Name }
    else { Fail 'Give the collector''s name: -Name <name>.' }
}
if ($Name -cnotmatch '^[a-z0-9][a-z0-9-]{0,62}$' -or $Name -eq 'default') {
    Fail 'The collector''s name: lower case letters, digits and dashes, and not ''default''.'
}
$Id = "blt-collector-$Name"
$Data = Join-Path $DataRoot $Name
$SettingsFile = Join-Path $Data 'collector.json'
$Logs = Join-Path $Data 'logs'
$ServerCertificate = Join-Path $Data 'blt-server.pem'
$CaBundle = Join-Path $Data 'ca-bundle.pem'
$Wrapper = Get-ServiceWrapper $Data $Id

# --- Uninstall ------------------------------------------------------------
if ($Uninstall) {
    $service = Stop-Collector $Id
    if ($service) {
        if (Test-Path -LiteralPath $Wrapper) { Invoke-Wrapper $Wrapper 'uninstall' }
        else { & sc.exe delete $Id | Out-Null }
        for ($i = 0; $i -lt 30 -and (Get-Service -Name $Id -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }
    }
    if (Test-Path -LiteralPath $Data) { Remove-Item -LiteralPath $Data -Recurse -Force }
    Remove-UnusedVersions ''
    if (-not (Get-ChildItem -LiteralPath $DataRoot -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $DataRoot -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $ProgramRoot) { Remove-Item -LiteralPath $ProgramRoot -Recurse -Force }
    }
    Write-Host "Collector $Name removed, with its settings and logs."
    exit 0
}

# --- The settings it had, if any (an upgrade) --------------------------------
$old = $null
if (Test-Path -LiteralPath $SettingsFile) {
    $old = Get-Content -LiteralPath $SettingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Get-Old([string]$Key) {
    if ($old -and ($old.PSObject.Properties.Name -contains $Key)) { return [string]$old.$Key }
    return ''
}
if (-not $Url) { $Url = Get-Old 'BLT_COLLECTOR_SERVER' }
if (-not $Url) { Fail 'Give blt''s address: -Url https://<server>.' }
$Url = $Url.TrimEnd('/')
if ($Url -notmatch '^https://[^/\s]+$') {
    Fail "-Url must be https://<server> (or https://<server>:<port>), with no path; not $Url."
}
$server = [Uri]$Url
if (-not $WorkPool) { $WorkPool = Get-Old 'BLT_WORK_POOL' }
if (-not $WorkPool) { $WorkPool = 'blt' }

# --- What it needs from you: asked before anything is changed -------------
$auth = Get-Old 'PREFECT_API_AUTH_STRING'
$key = Get-Old 'BLT_API_KEY'
if ($Reconfigure -or -not $auth) {
    $auth = Read-Secret 'Prefect''s user:password (PREFECT_AUTH in blt.env)' 'BLT_COLLECTOR_PREFECT_AUTH'
    if ($auth -notmatch ':') { Fail 'Prefect''s credentials are user:password, as PREFECT_AUTH in blt''s config/blt.env.' }
}
if ($Reconfigure -or -not $key) {
    $key = Read-Secret 'blt''s API key (BLT_API_KEY in blt.env)' 'BLT_COLLECTOR_API_KEY'
    if (-not $key) { Fail 'blt''s API key is needed.' }
}

# blt's certificate, when it is not one Python already trusts.
$certificate = $null
$trusted = $null
if ($CertificateFile) {
    if (-not (Test-Path -LiteralPath $CertificateFile)) { Fail "No such file: $CertificateFile" }
    $text = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $CertificateFile))
    if ($text.Contains('-----BEGIN CERTIFICATE-----')) {
        # PEM as it is: a CA's file may hold more than one.
        $pem = $text.Replace("`r`n", "`n")
        $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
            , [byte[]][Convert]::FromBase64String(($text -split '-----')[2]))
    }
    else {
        $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
            (Resolve-Path -LiteralPath $CertificateFile).Path)
        $pem = ConvertTo-Pem $certificate
    }
    Write-Host "Trusting $($certificate.Subject) (SHA-256 $(Get-Fingerprint $certificate)) from $CertificateFile."
    $trusted = $pem
}
elseif ($TrustCertificate) {
    $port = $server.Port
    Write-Host "Fetching the certificate of $($server.Host):$port..."
    try { $certificate = Get-ServerCertificate $server.Host $port }
    catch { Fail "Could not reach $($server.Host):$port over TLS: $($_.Exception.Message)" }
    $fingerprint = Get-Fingerprint $certificate
    Write-Host "  Subject:  $($certificate.Subject)"
    Write-Host "  Issuer:   $($certificate.Issuer)"
    Write-Host "  Expires:  $($certificate.NotAfter)"
    Write-Host "  SHA-256:  $fingerprint"
    if ($CertificateFingerprint) {
        $want = ($CertificateFingerprint -replace '^.*=', '' -replace '[^0-9A-Fa-f]', '').ToUpperInvariant()
        if ($want -ne $fingerprint.Replace(':', '')) {
            Fail 'That is not the fingerprint given with -CertificateFingerprint: not trusted. Is something in between?'
        }
    }
    else {
        Write-Host 'Compare it with the fingerprint deploy/up.sh printed on blt''s server.'
        if ((Read-Host 'Trust this certificate for the collector? [y/N]') -notmatch '^[yY]') { Fail 'Not trusted; nothing changed.' }
    }
    $trusted = ConvertTo-Pem $certificate
}

# --- blt itself, and its Python ------------------------------------------
$Version = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'VERSION') -Raw).Trim()
$Target = Join-Path $ProgramRoot $Version
$Python = Join-Path $Target 'python\python.exe'
$payload = Join-Path $PSScriptRoot 'payload.zip'

# The service runs from these files: stop it before replacing them.
$service = Stop-Collector $Id
if (Test-Path -LiteralPath (Join-Path $Target 'VERSION')) {
    # This version is installed already (re-run, -Reconfigure, or a second
    # collector on this server, which may be running from it).
    Write-Host "blt $Version is installed in $Target already."
}
elseif (Test-Path -LiteralPath $payload) {
    Write-Host "Installing blt $Version to $Target..."
    if (-not (Test-Path -LiteralPath $ProgramRoot)) { New-Item -ItemType Directory -Path $ProgramRoot | Out-Null }
    # Unpacked beside it and renamed when complete: a half-unpacked
    # folder is never taken for an installed version.
    $fresh = "$Target.new"
    if (Test-Path -LiteralPath $fresh) { Remove-Item -LiteralPath $fresh -Recurse -Force }
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($payload, $fresh)
    foreach ($file in @('install.ps1', 'blt-collector.xml', 'README.txt')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $fresh
    }
    Rename-Item -LiteralPath $fresh -NewName $Version
}
else {
    Fail "No payload.zip beside this script, and no blt $Version in $Target. Run install.ps1 from the unpacked bundle."
}

# --- Its settings ----------------------------------------------------------
Protect-Folder $DataRoot
Protect-Folder $Data
if (-not (Test-Path -LiteralPath $Logs)) { New-Item -ItemType Directory -Path $Logs | Out-Null }

if ($trusted) { Write-Text $ServerCertificate $trusted }

$settings = [ordered]@{
    BLT_COLLECTOR                     = $Name
    BLT_WORK_POOL                     = $WorkPool
    BLT_COLLECTOR_SERVER              = $Url
    PREFECT_API_URL                   = "$Url/prefect/api"
    PREFECT_API_AUTH_STRING           = $auth
    BLT_API_URL                       = "$Url/api"
    BLT_API_KEY                       = $key
    PREFECT_HOME                      = (Join-Path $Data 'prefect')
    # As in the worker image: no usage data to Prefect, and a heartbeat
    # from each flow run every 30 s so a dead one is marked Crashed soon.
    DO_NOT_TRACK                      = '1'
    PREFECT_FLOWS_HEARTBEAT_FREQUENCY = '30'
}
if (Test-Path -LiteralPath $ServerCertificate) {
    # Python's usual CAs (certifi's, as without this) and blt's certificate:
    # the collector also talks to Rubrik, NetBackup and Commvault, whose
    # certificates must still be checked as before. Made again on every
    # install, so an upgrade brings certifi's newer list along.
    $certifi = Join-Path $Target 'python\Lib\site-packages\certifi\cacert.pem'
    $bundle = [System.IO.File]::ReadAllText($certifi).Replace("`r`n", "`n").TrimEnd() + "`n`n# blt's server`n" +
        [System.IO.File]::ReadAllText($ServerCertificate)
    Write-Text $CaBundle $bundle
    $settings['SSL_CERT_FILE'] = $CaBundle
    $settings['PREFECT_API_SSL_CERT_FILE'] = $CaBundle
}
Write-Text $SettingsFile ($settings | ConvertTo-Json)
$auth = $null
$key = $null
$settings = $null

# --- Can it reach blt? -----------------------------------------------------
if (-not $SkipCheck) {
    Write-Host "Checking that Prefect and blt's API answer at $Url..."
    & $Python -m blt.flows.collector --settings $SettingsFile --check
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Not yet (above). The service is installed anyway, and keeps trying until they do.' -ForegroundColor Yellow
    }
}

# --- The service -----------------------------------------------------------
Copy-Item -LiteralPath (Join-Path $Target 'blt-collector-service.exe') -Destination $Wrapper -Force
$xml = [System.IO.File]::ReadAllText((Join-Path $Target 'blt-collector.xml'))
$values = @{
    '@NAME@'     = $Name
    '@VERSION@'  = $Version
    '@SERVER@'   = $Url
    '@PYTHON@'   = $Python
    '@SETTINGS@' = $SettingsFile
    '@DATA@'     = $Data
    '@LOGS@'     = $Logs
}
foreach ($placeholder in $values.Keys) {
    $xml = $xml.Replace($placeholder, [System.Security.SecurityElement]::Escape($values[$placeholder]))
}
Write-Text ([System.IO.Path]::ChangeExtension($Wrapper, '.xml')) $xml

if ($service) {
    # WinSW 2.12 has no refresh: registered again, so a new version's
    # start mode and restart settings take effect too.
    Invoke-Wrapper $Wrapper 'uninstall'
    for ($i = 0; $i -lt 30 -and (Get-Service -Name $Id -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }
    if (Get-Service -Name $Id -ErrorAction SilentlyContinue) {
        Fail "$Id is still marked for deletion: close the Services window (or anything else holding it open) and run this again."
    }
}
Invoke-Wrapper $Wrapper 'install'
Invoke-Wrapper $Wrapper 'start'
Remove-UnusedVersions $Target

Write-Host ''
Write-Host "Collector $Name (blt $Version) is running as the service $Id." -ForegroundColor Green
Write-Host "  Settings: $SettingsFile (SYSTEM and Administrators only)"
Write-Host "  Logs:     $Logs\$Id.out.log and $Id.err.log"
Write-Host "In blt: Administration -> Sources, where $Name appears under Collectors once it is online."
