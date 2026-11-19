$ErrorActionPreference = 'Stop'
$meridianKey = Read-Host 'Paste the new Odoo RPC API key and press Enter' -AsSecureString
$meridianCredential = [pscredential]::new('odoo', $meridianKey)
$meridianPreviousKey = [Environment]::GetEnvironmentVariable('ODOO_API_KEY', 'Process')

try {
    $env:ODOO_API_KEY = $meridianCredential.GetNetworkCredential().Password.Trim()
    if (-not $env:ODOO_API_KEY) {
        throw 'No Odoo RPC API key was entered.'
    }
    & python -B (Join-Path $PSScriptRoot 'app.py') --live --seed-odoo --port 8766 --db (Join-Path $PSScriptRoot 'source-currency.sqlite3')
    if ($LASTEXITCODE -ne 0) {
        throw "Meridian exited with code $LASTEXITCODE."
    }
} finally {
    [Environment]::SetEnvironmentVariable('ODOO_API_KEY', $meridianPreviousKey, 'Process')
    $meridianKey.Dispose()
}
