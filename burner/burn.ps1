# DiscDrop CD burner
# Uses Windows' built-in IMAPI2 (no extra software needed).
#
# Usage:
#   Detect drives:           burn.ps1 -Mode Audio -CheckOnly
#   Burn audio CD:           burn.ps1 -Mode Audio -Device D: -StagingDir <dir with .wav files>
#   Burn MP3 data disc:      burn.ps1 -Mode Data  -Device D: -StagingDir <dir with mp3 files>
#
# Output (CheckOnly) is JSON on stdout so the backend can parse it.
param(
    [ValidateSet("Audio", "Data")]
    [string]$Mode = "Audio",
    [string]$Device = "",
    [string]$StagingDir = "",
    [int]$SpeedKB = 0,
    [switch]$Eject,
    [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"

function Get-DeviceString($letter) {
    # "D:" -> "\\.\D:"  (IMAPI2 wants the \\.\ prefix)
    return "\\.\" + $letter
}

# Get a recorder for a drive letter, initialized with the drive's UNIQUE device
# ID (from MsftDiscMaster2). Using \\.\X: works for reading properties but fails
# PrepareMedia with E_HANDLE on many USB writers, so we always use the unique ID.
function Get-RecorderForLetter($letter) {
    $master = New-Object -ComObject IMAPI2.MsftDiscMaster2
    for ($i = 0; $i -lt $master.Count; $i++) {
        $uid = $master[$i]
        $rec = New-Object -ComObject IMAPI2.MsftDiscRecorder2
        $rec.InitializeDiscRecorder($uid)
        foreach ($p in @($rec.VolumePathNames)) {
            if ($p -like "$letter*") {
                return $rec
            }
        }
    }
    return $null
}

function Write-Json($obj) {
    $obj | ConvertTo-Json -Compress
}

# ---------------------------------------------------------------- drives
function Get-Drives {
    $result = @()
    Get-CimInstance Win32_CDROMDrive | ForEach-Object {
        $letter = $_.Drive
        if ($letter) {
            $result += [PSCustomObject]@{
                letter      = $letter
                description = "$($_.Name)"
                mediaType   = $_.MediaType
            }
        }
    }
    return $result
}

if ($CheckOnly) {
    $drives = @(Get-Drives)
    if ($drives.Count -eq 0) {
        Write-Json @{ ok = $false; error = "No CD/DVD drives found on this PC." }
        exit 1
    }
    $letter = if ($Device) { $Device } else { $drives[0].letter }
    $driveInfo = $drives | Where-Object { $_.letter -eq $letter }
    if (-not $driveInfo) {
        Write-Json @{ ok = $false; error = "Drive $letter not found." }
        exit 1
    }
    try {
        $recorder = Get-RecorderForLetter $letter
        if (-not $recorder) {
            Write-Json @{ ok = $false; error = "Could not open writer for drive $letter." }
            exit 1
        }
        $cim = Get-CimInstance Win32_CDROMDrive | Where-Object { $_.Drive -eq $letter }
        $loaded = if ($cim) { $cim.MediaLoaded } else { $false }
        $blank = $null
        $freeSectors = $null
        if ($loaded) {
            $format = New-Object -ComObject IMAPI2.MsftDiscFormat2TrackAtOnce
            $format.Recorder = $recorder
            $blank = $format.MediaHeuristicallyBlank
            $freeSectors = $format.FreeSectorsOnMedia
        }
    } catch {
        Write-Json @{ ok = $false; error = $_.Exception.Message }
        exit 1
    }
    Write-Json @{
        ok       = $true
        drives   = $drives
        selected = $letter
        mediaLoaded = $loaded
        blank    = $blank
        freeSectors = $freeSectors
    }
    exit 0
}

# ---------------------------------------------------------------- burn
if (-not $Device) {
    Write-Error "No -Device given (e.g. D:)."
    exit 1
}
if (-not $StagingDir -or -not (Test-Path -LiteralPath $StagingDir)) {
    Write-Error "StagingDir missing or does not exist: $StagingDir"
    exit 1
}

$recorder = Get-RecorderForLetter $Device
if (-not $recorder) {
    Write-Error "Could not open writer for drive $Device."
    exit 1
}

if ($Mode -eq "Audio") {
    # --- standard CD-DA: one track per raw PCM file (44.1kHz / 16-bit / stereo) ---
    $pcmFiles = @(Get-ChildItem -LiteralPath $StagingDir -Filter *.pcm | Sort-Object Name)
    if ($pcmFiles.Count -eq 0) {
        Write-Error "No .pcm files in $StagingDir"
        exit 1
    }
    $format = New-Object -ComObject IMAPI2.MsftDiscFormat2TrackAtOnce
    $format.Recorder = $recorder
    $format.ClientName = "DiscDrop"

    if (-not $format.MediaHeuristicallyBlank) {
        Write-Error "Disc in $Device is not blank. Insert a blank CD-R/RW."
        exit 1
    }

    # capacity check: 2352 bytes/sector, plus ~2s (150 sectors) gap per track
    $neededSectors = 0
    foreach ($pcm in $pcmFiles) {
        $neededSectors += [math]::Ceiling((Get-Item -LiteralPath $pcm.FullName).Length / 2352) + 150
    }
    $freeSectors = $format.FreeSectorsOnMedia
    if ($freeSectors -gt 0 -and $neededSectors -gt $freeSectors) {
        Write-Error "Songs need $neededSectors sectors but disc only has $freeSectors free."
        exit 1
    }

    if ($SpeedKB -gt 0) {
        try {
            $format.SetWriteSpeed($SpeedKB, $false)
            Write-Output "SPEED ${SpeedKB}KB/s ($([math]::Round($SpeedKB / 176.4, 1))x)"
        } catch {
            Write-Output "WARN: drive ignored requested speed: $($_.Exception.Message)"
        }
    }
    $format.PrepareMedia()
    $i = 0
    foreach ($pcm in $pcmFiles) {
        $i++
        Write-Output "TRACK $i/$($pcmFiles.Count) $($pcm.Name)"
        $stream = New-Object -ComObject ADODB.Stream
        $stream.Type = 1
        $stream.Open()
        $stream.LoadFromFile($pcm.FullName)
        $format.AddAudioTrack($stream)
        $stream.Close()
    }
    $format.ReleaseMedia()
    if ($Eject) { $recorder.EjectMedia() }
    Write-Output "BURN COMPLETE"
    Write-Output "OK"
    exit 0
}
else {
    # --- MP3 data disc (Joliet, most compatible with old head units) ---
    $files = @(Get-ChildItem -LiteralPath $StagingDir -File)
    if ($files.Count -eq 0) {
        Write-Error "No files in $StagingDir"
        exit 1
    }
    $writer = New-Object -ComObject IMAPI2.MsftDiscFormat2Data
    $writer.Recorder = $recorder
    $writer.ClientName = "DiscDrop"
    if (-not $writer.MediaHeuristicallyBlank) {
        Write-Error "Disc in $Device is not blank. Insert a blank CD-R/RW."
        exit 1
    }
    $fs = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $fs.FileSystemsToCreate = 3   # ISO9660 + Joliet (Joliet alone is rejected)
    $fs.ChooseImageDefaultsForMediaType(2)  # CD-R
    $fs.Root.AddTree($StagingDir, $false)
    $writer.Write($fs)
    if ($Eject) { $recorder.EjectMedia() }
    Write-Output "BURN COMPLETE"
    Write-Output "OK"
    exit 0
}