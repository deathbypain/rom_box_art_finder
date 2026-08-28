###############################################################################
# download_covers.ps1
# Downloads missing cover art from libretro-thumbnails for R36S SD card
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File download_covers.ps1
#   powershell -ExecutionPolicy Bypass -File download_covers.ps1 -Platform GBA
#   powershell -ExecutionPolicy Bypass -File download_covers.ps1 -RootPath "D:\"
#
# The script uses the GitHub API to list all available boxarts for each
# platform, then fuzzy-matches ROM names against the available thumbnails.
###############################################################################
# https://raw.githubusercontent.com/libretro-thumbnails/{PLATFORM}/refs/heads/master/Named_Boxarts/{GAME_TITLE}.png
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RootPath,
    [string]$Platform = ""
)

try {
    if ([string]::IsNullOrWhiteSpace($RootPath)) {
        throw "RootPath is required and cannot be empty."
    }

    if (-not (Test-Path -Path $RootPath -PathType Container)) {
        throw "RootPath '$RootPath' does not exist or is not a directory."
    }
} catch {
    Write-Error "RootPath validation failed: $($_.Exception.Message)"
    exit 1
}

Add-Type -AssemblyName System.Drawing

# Mapping: local folder -> libretro-thumbnails repo name
$repoMap = @{
    'A26'   = 'Atari_-_2600'
    'FC'    = 'Nintendo_-_Nintendo_Entertainment_System'
    'GB'    = 'Nintendo_-_Game_Boy'
    'GBA'   = 'Nintendo_-_Game_Boy_Advance'
    'GBC'   = 'Nintendo_-_Game_Boy_Color'
    'GG'    = 'Sega_-_Game_Gear'
    'MAME'  = 'MAME'
    'MD'    = 'Sega_-_Mega_Drive_-_Genesis'
    'NGPC'  = 'SNK_-_Neo_Geo_Pocket_Color'
    'PCE'   = 'NEC_-_PC_Engine_-_TurboGrafx_16'
    'PS'    = 'Sony_-_PlayStation'
    'SFC'   = 'Nintendo_-_Super_Nintendo_Entertainment_System'
}

# ROM extensions per platform
$romExtMap = @{
    'A26'   = @('.a26', '.a78', '.bin', '.zip', '.7z')
    'FC'    = @('.nes', '.fds', '.zip', '.7z')
    'GB'    = @('.gb', '.zip', '.7z')
    'GBA'   = @('.gba', '.zip', '.7z')
    'GBC'   = @('.gbc', '.zip', '.7z')
    'GG'    = @('.gg', '.sms', '.zip', '.7z')
    'MAME'  = @('.fba', '.zip', '.7z')
    'MD'    = @('.md', '.gen', '.bin', '.zip', '.7z', '.smd')
    'NGPC'  = @('.ngp', '.ngc', '.zip', '.7z')
    'PCE'   = @('.pce', '.zip', '.7z')
    'PS'    = @('.img', '.iso', '.bin', '.cue', '.pbp', '.chd', '.zip', '.7z')
    'SFC'   = @('.sfc', '.smc', '.zip', '.7z')
}

$baseUrl = "https://raw.githubusercontent.com/libretro-thumbnails"

# Extract keywords from a name (2+ alphanumeric chars, lowercased)
function Get-Keywords($name) {
    $clean = $name -replace '_', ' '
    $words = [regex]::Matches($clean, '[a-zA-Z0-9]{2,}') | ForEach-Object { $_.Value.ToLower() }
    # Filter out common noise words
    $noise = @('the', 'of', 'and', 'in', 'to', 'for', 'usa', 'europe', 'japan', 'world',
               'rev', 'en', 'fr', 'de', 'es', 'it', 'proto', 'beta', 'demo', 'unl',
               'sfc', 'nes', 'snes', 'gba', 'gbc')
    return $words | Where-Object { $noise -notcontains $_ }
}

# Score how well a ROM name matches a thumbnail name
function Get-MatchScore($romName, $thumbName) {
    $romWords = Get-Keywords $romName
    $thumbWords = Get-Keywords $thumbName
    if ($romWords.Count -eq 0 -or $thumbWords.Count -eq 0) { return 0 }

    $score = 0
    $matched = 0
    foreach ($rw in $romWords) {
        foreach ($tw in $thumbWords) {
            if ($rw -eq $tw) {
                $score += 3
                $matched++
                break
            } elseif ($tw.Contains($rw) -or $rw.Contains($tw)) {
                $score += 1
                $matched++
                break
            }
        }
    }

    # Require that most ROM keywords matched (at least 80%)
    if ($romWords.Count -gt 0) {
        $ratio = $matched / $romWords.Count
        if ($ratio -lt 0.8) { return 0 }
    }

    return $score
}

# Fetch available thumbnails for a repo via GitHub API
function Get-AvailableThumbnails($repo) {
    $apiUrl = "https://api.github.com/repos/libretro-thumbnails/$repo/git/trees/master?recursive=1"
    try {
        $response = Invoke-RestMethod -Uri $apiUrl -Method Get -Headers @{ 'User-Agent' = 'SF3000-Cover-Downloader' } -ErrorAction Stop
        $thumbs = @()
        foreach ($item in $response.tree) {
            if ($item.path -match '^Named_Boxarts/(.+)\.png$') {
                $thumbs += $Matches[1]
            }
        }
        return $thumbs
    } catch {
        Write-Output "    GITHUB API ERROR: $($_.Exception.Message)"
        return @()
    }
}

# Download and resize a thumbnail
function Download-Thumbnail($repo, $thumbName, $destPath) {
    $encodedName = [Uri]::EscapeDataString($thumbName)
    $url = "$baseUrl/$repo/master/Named_Boxarts/$encodedName.png"
    try {
        $tempFile = [System.IO.Path]::GetTempFileName()
        $webClient = New-Object System.Net.WebClient
        $webClient.DownloadFile($url, $tempFile)
        $webClient.Dispose()

        # Resize to 320x240
        $img = [System.Drawing.Image]::FromFile($tempFile)
        $bmp = New-Object System.Drawing.Bitmap(320, 240, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality

        $ratioX = 320 / $img.Width
        $ratioY = 240 / $img.Height
        $ratio = [Math]::Min($ratioX, $ratioY)
        $newW = [int]($img.Width * $ratio)
        $newH = [int]($img.Height * $ratio)
        $posX = [int]((320 - $newW) / 2)
        $posY = [int]((240 - $newH) / 2)

        $g.Clear([System.Drawing.Color]::White)
        $g.DrawImage($img, $posX, $posY, $newW, $newH)
        $g.Dispose()
        $img.Dispose()

        $bmp.Save($destPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()

        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        Remove-Item $tempFile -Force -ErrorAction SilentlyContinue
        return $false
    }
}

# Download a candidate thumbnail to a temp file and open it in the system default viewer
function Show-ClosestMatchPreview($repo, $thumbName, $romBaseName, $destPath) {
    $previewPath = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "libretro-preview-$($romBaseName).png")
    $previewPath = $previewPath -replace '[\\/:*?"<>|]', '_'

    $ok = Download-Thumbnail $repo $thumbName $previewPath
    if (-not $ok) {
        Write-Output "    Preview download failed for $thumbName"
        return $false
    }

    try {
        Start-Process $previewPath
        Write-Output "    Opened preview in the default image viewer: $thumbName"
    } catch {
        Write-Output "    Could not open preview: $($_.Exception.Message)"
    }

    $answer = Read-Host "      Accept this preview and save it to $destPath? [Y/N]"
    if ($answer -match '^(y|yes)$') {
        try {
            if (-not (Test-Path -Path (Split-Path $destPath -Parent))) {
                New-Item -ItemType Directory -Path (Split-Path $destPath -Parent) -Force | Out-Null
            }
            Copy-Item -Path $previewPath -Destination $destPath -Force
            Write-Output "    Saved approved image to: $destPath"
            return $true
        } catch {
            Write-Output "    Save failed: $($_.Exception.Message)"
            return $false
        }
    }

    return $false
}

# Find best matching thumbnail for a ROM and keep the score so we can report close misses
function Find-BestMatch($romBaseName, $thumbnails) {
    $bestScore = 0
    $bestMatch = $null

    # Clean ROM name: remove region tags for matching
    $cleanRom = $romBaseName -replace '\s*\([^)]*\)', ''
    $cleanRom = $cleanRom.Trim()

    foreach ($thumb in $thumbnails) {
        $score = Get-MatchScore $cleanRom $thumb
        if ($score -gt $bestScore) {
            $bestScore = $score
            $bestMatch = $thumb
        }
    }

    if ($null -eq $bestMatch) {
        return $null
    }

    return [pscustomobject]@{
        Match = $bestMatch
        Score = $bestScore
        IsGoodMatch = ($bestScore -ge 8)
    }
}

###############################################################################
# Main
###############################################################################

# Determine which platforms to process
if ($Platform -ne "") {
    $platformList = @($Platform.ToUpper())
} else {
    $platformList = $repoMap.Keys | Sort-Object
}

$totalDownloaded = 0
$totalMissing = 0

foreach ($plat in $platformList) {
    if (-not $repoMap.ContainsKey($plat)) {
        Write-Output "Unknown platform: $plat"
        continue
    }

    $dirPath = Join-Path $RootPath $plat
    if (-not (Test-Path $dirPath)) { continue }

    $exts = $romExtMap[$plat]
    $imagesPath = Join-Path $dirPath ".res"
    $repo = $repoMap[$plat]

    # Get ROMs (filter PS tracks)
    $roms = Get-ChildItem $dirPath -File -Force -ErrorAction SilentlyContinue | Where-Object { $exts -contains $_.Extension.ToLower() } | Sort-Object Name
    if ($plat -eq 'PS' -and $roms.Count -gt 0) {
        $cueFiles = $roms | Where-Object { $_.Extension.ToLower() -eq '.cue' }
        $cueBaseNames = @{}
        foreach ($c in $cueFiles) { $cueBaseNames[$c.BaseName] = $true }
        $filtered = New-Object System.Collections.ArrayList
        foreach ($rom in $roms) {
            if ($rom.Extension.ToLower() -eq '.cue') {
                [void]$filtered.Add($rom)
            } elseif ($rom.Extension.ToLower() -eq '.bin') {
                $binBase = $rom.BaseName -replace ' \(Track \d+\)', ''
                if (-not $cueBaseNames.ContainsKey($binBase)) { [void]$filtered.Add($rom) }
            } else {
                [void]$filtered.Add($rom)
            }
        }
        $roms = $filtered
    }

    if ($roms.Count -eq 0) { continue }

    # Find ROMs without covers
    $missing = @()
    foreach ($rom in $roms) {
        $imgFile = Join-Path $imagesPath "$($rom.BaseName).png"
        if (-not (Test-Path $imgFile)) { $missing += $rom }
    }

    if ($missing.Count -eq 0) { continue }

    Write-Output ""
    Write-Output "=== $plat : $($missing.Count) covers missing ==="

    # Fetch available thumbnails from GitHub
    Write-Output "    Loading libretro-thumbnails/$repo list..."
    $thumbnails = Get-AvailableThumbnails $repo
    if ($thumbnails.Count -eq 0) {
        Write-Output "    No thumbnails available (API error or empty repo)"
        continue
    }
    Write-Output "    $($thumbnails.Count) boxarts available"

    if (-not (Test-Path $imagesPath)) {
        New-Item -ItemType Directory -Path $imagesPath -Force | Out-Null
    }

    foreach ($rom in $missing) {
        $destPath = Join-Path $imagesPath "$($rom.BaseName).png"

        # Find best match via fuzzy keyword matching
        $matchResult = Find-BestMatch $rom.BaseName $thumbnails

        if ($matchResult -and $matchResult.IsGoodMatch) {
            $ok = Download-Thumbnail $repo $matchResult.Match $destPath
            if ($ok) {
                Write-Output "  OK: $($rom.BaseName)"
                Write-Output "      -> $($matchResult.Match)"
                $totalDownloaded++
            } else {
                Write-Output "  DOWNLOAD FAIL: $($rom.BaseName) -> $($matchResult.Match)"
                $totalMissing++
            }
        } elseif ($matchResult) {
            Write-Output "  NO GOOD MATCH: $($rom.BaseName)"
            Write-Output "      Closest match: $($matchResult.Match) (score: $($matchResult.Score))"
            Write-Output "      Suggested assignment: assign this image to $($matchResult.Match)"

            $answer = Read-Host "      Open preview for this closest match in the default image viewer? [Y/N]"
            if ($answer -match '^(y|yes)$') {
                $approved = Show-ClosestMatchPreview $repo $matchResult.Match $rom.BaseName $destPath
                if ($approved) {
                    $totalDownloaded++
                }
            }

            $totalMissing++
        } else {
            Write-Output "  UNAVAILABLE: $($rom.BaseName)"
            $totalMissing++
        }
    }
}

Write-Output ""
Write-Output "============================================"
Write-Output "Downloaded: $totalDownloaded | Unavailable: $totalMissing"
Write-Output "============================================"
