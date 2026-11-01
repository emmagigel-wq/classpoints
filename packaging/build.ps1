# build.ps1 — 打应用本体（onedir）。UTF-8 带 BOM；被 build_installer.ps1 复用。
# 依据 README「打包成安装包」与 classpoints.spec 头注重建。
param(
    [string]$Python = 'C:\Python314\python.exe',
    [switch]$SkipJunction
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')   # classpoints-py/

# 用户名含中文时 Qt 会解析出 C:/Users/?/…：用纯 ASCII 目录联接绕开（README 原话）
if (-not $SkipJunction -and $env:USERPROFILE -match '[^\x00-\x7F]') {
    if (-not (Test-Path 'C:\cpbuild')) { cmd /c "mklink /J C:\cpbuild `"$env:USERPROFILE`"" | Out-Null }
    $env:HOME = 'C:\cpbuild'
    $env:APPDATA = 'C:\cpbuild\AppData\Roaming'
    $env:LOCALAPPDATA = 'C:\cpbuild\AppData\Local'
    $env:PYTHONHOME = 'C:\cpbuild'
    Write-Host '[build] 使用 ASCII 目录联接 C:\cpbuild（用户名含中文）'
}

& $Python -m PyInstaller --noconfirm --clean packaging\classpoints.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller(classpoints) 失败: $LASTEXITCODE" }

# 随包组件检查（缺失只警告，不阻断——与 install_core._verify_install 的口径一致）
foreach ($f in @('dist\班级积分系统\_internal\cloudflared.exe', 'dist\班级积分系统\_internal\assets\ffmpeg.exe')) {
    if (-not (Test-Path $f)) { Write-Warning "缺少随包组件 $f（公网穿透/转码会提示下载）" }
}
Write-Host '[build] onedir 产物就绪：dist\班级积分系统\'
