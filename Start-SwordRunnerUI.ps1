param(
    [string]$ProjectRoot = $env:SWORD_RUNNER_PROJECT_ROOT
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Read-Host "Enter the audiobook project folder (must contain book.txt and chapter_index.json)"
}
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    throw "Project folder is required. Pass -ProjectRoot or set SWORD_RUNNER_PROJECT_ROOT."
}
$projectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$script = Join-Path $PSScriptRoot "sword_runner\web_ui.py"

Write-Host "[sword-runner-ui] starting local UI"
Write-Host "[sword-runner-ui] project_root=$projectRoot"
Write-Host "[sword-runner-ui] url=http://127.0.0.1:8765"

python -u -X utf8 $script --project-root $projectRoot --host 127.0.0.1 --port 8765

