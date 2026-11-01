# build_installer.ps1 — 一条龙：应用 onedir → Uninstall.exe → payload.zip+manifest →
# 单个 setup.exe。UTF-8 带 BOM（README：.ps1 必须带 BOM，_tools/ensure_bom.py 可自检）。
# 依据 setup.exe / app.exe 取证结构与 installer/build_payload.py（逐指令还原件）重建。
param(
    [string]$Python = 'C:\Python314\python.exe',
    [string]$Version = '',
    [switch]$BuildApp = $true
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'
Set-Location $root

if ($BuildApp) {
    & "$PSScriptRoot\build.ps1" -Python $Python
    if ($LASTEXITCODE -ne 0) { throw "build.ps1 失败: $LASTEXITCODE" }
}

# 1) 卸载器（必须早于 build_payload —— 它会警告「还没有 Uninstall.exe（先打卸载器）」）
& $Python -m PyInstaller --noconfirm --clean packaging\uninstaller.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller(uninstaller) 失败: $LASTEXITCODE" }

# 2) 压 payload + manifest（版本号缺省由它自己从 app/version.py 读）
New-Item -ItemType Directory -Force build\installer | Out-Null
Copy-Item dist\Uninstall\Uninstall.exe build\installer\Uninstall.exe -Force
$bpArgs = @('installer\build_payload.py',
            '--dist', 'dist\班级积分系统',
            '--out', 'build\installer')
if ($Version) { $bpArgs += @('--version', $Version) }
& $Python @bpArgs
if ($LASTEXITCODE -ne 0) { throw "build_payload 失败: $LASTEXITCODE" }

# 3) 安装器 onefile。
# 关键：不能使用默认 build 工作目录，因为 build\installer 中存放着上一步生成的 payload.zip。
# 将 PyInstaller 的临时工作目录隔离，--clean 就不会清掉安装器资源。
& $Python -m PyInstaller --noconfirm --clean --workpath build\pyinstaller-installer packaging\installer.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller(installer) 失败: $LASTEXITCODE" }

if (-not $Version) {
    $Version = (& $Python -c "import sys;sys.path.insert(0,'.');from app import version;print(version.__version__)").Trim()
}
$out = "dist\班级积分系统-安装程序-v$Version.exe"
Copy-Item dist\setup.exe $out -Force
Write-Host "[installer] 完成：$out（$([math]::Round((Get-Item $out).Length/1MB,1)) MB）"
