python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host ""
    Write-Host "Created .env — open it and add your GOOGLE_API_KEY before starting the server."
}

Write-Host ""
Write-Host "Done. To start the server:"
Write-Host "  .venv\Scripts\Activate.ps1; python app.py"
