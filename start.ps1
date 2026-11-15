# Starts DocIntel locally without Docker: FastAPI backend (port 8000) + Next.js web app (port 3000).
# First run creates backend\.venv and installs dependencies. Usage:  .\start.ps1
# If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "Created .env from .env.example. Add your Gemini key (LLM_API_KEY) to turn on AI extraction." -ForegroundColor Yellow
}

$py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv (Join-Path $backend ".venv") } else { python -m venv (Join-Path $backend ".venv") }
    & $py -m pip install --upgrade pip
    & $py -m pip install -r (Join-Path $backend "requirements.txt")
}

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing web app dependencies..." -ForegroundColor Cyan
    Push-Location $frontend; npm install; Pop-Location
}

if (-not (Get-Command tesseract -ErrorAction SilentlyContinue) -and -not (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")) {
    Write-Host "Tesseract not found: scanned PDFs and images will fail. Install with: winget install UB-Mannheim.TesseractOCR" -ForegroundColor Yellow
}

Write-Host "Starting API on http://localhost:8000 (docs at /docs)" -ForegroundColor Green
$api = Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000" -WorkingDirectory $backend -PassThru -NoNewWindow

try {
    Write-Host "Starting web app on http://localhost:3000" -ForegroundColor Green
    Push-Location $frontend
    npm run dev
} finally {
    Pop-Location
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
