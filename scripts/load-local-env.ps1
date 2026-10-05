param(
  [string]$EnvironmentFile = (Join-Path (Split-Path $PSScriptRoot -Parent) '.env')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
  throw 'Local .env is missing. First run: uv run --locked --group dev python scripts/local_stack.py prepare'
}

foreach ($line in Get-Content -LiteralPath $EnvironmentFile) {
  $entry = $line.Trim()
  if (-not $entry -or $entry.StartsWith('#')) { continue }
  $separator = $entry.IndexOf('=')
  if ($separator -lt 1) { throw 'Invalid entry in local .env file' }
  $name = $entry.Substring(0, $separator)
  $value = $entry.Substring($separator + 1)
  if ($name -notmatch '^[A-Z][A-Z0-9_]*$') { throw 'Invalid environment variable name in local .env file' }
  Set-Item -Path "Env:$name" -Value $value
}

if (-not $env:PCB_LOCAL_S3_ACCESS_KEY -or -not $env:PCB_LOCAL_S3_SECRET_KEY) {
  throw 'Local object-store credentials are missing. Run scripts/local_stack.py prepare first.'
}
$env:AWS_ACCESS_KEY_ID = $env:PCB_LOCAL_S3_ACCESS_KEY
$env:AWS_SECRET_ACCESS_KEY = $env:PCB_LOCAL_S3_SECRET_KEY
