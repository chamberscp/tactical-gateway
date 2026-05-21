param(
    [Parameter(Position=0)]
    [string]$Target = "help"
)

$ComposeFile = "deploy/docker/compose.dev.yml"

function Invoke-DockerCompose {
    param([string[]]$ComposeArgs)
    docker compose -f $ComposeFile @ComposeArgs
}

switch ($Target) {
    "help" {
        Write-Host "Available targets:" -ForegroundColor Cyan
        Write-Host "  up         Bring up the dev stack"
        Write-Host "  down       Tear down the dev stack"
        Write-Host "  ps         Show container status"
        Write-Host "  logs       Tail logs from all services"
        Write-Host "  migrate    Run database migrations"
        Write-Host "  test       Run all tests"
        Write-Host "  lint       Run ruff lint checks"
        Write-Host "  format     Auto-format with ruff"
        Write-Host "  typecheck  Run mypy"
        Write-Host "  smoke      Run the smoke test"
        Write-Host "  clean      Remove containers, volumes, caches"
        Write-Host "  rebuild    Rebuild service images"
    }
    "up" {
        Invoke-DockerCompose @("up", "-d")
        Write-Host "Stack starting. Run .\make.ps1 ps to check status." -ForegroundColor Green
    }
    "down" {
        Invoke-DockerCompose @("down")
    }
    "ps" {
        Invoke-DockerCompose @("ps")
    }
    "logs" {
        Invoke-DockerCompose @("logs", "-f")
    }
    "migrate" {
        $env:POSTGRES_HOST = "localhost"
        $env:POSTGRES_PORT = "5432"
        $env:POSTGRES_DB = "gateway"
        $env:POSTGRES_USER = "gateway"
        $env:POSTGRES_PASSWORD = "gateway"
        python -m alembic upgrade head
    }
    "test" {
        pytest
    }
    "lint" {
        ruff check .
    }
    "format" {
        ruff format .
        ruff check --fix .
    }
    "typecheck" {
        mypy libs/ services/
    }
    "smoke" {
        $ok = $true
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 5
            if ($health.status -eq "ok") {
                Write-Host "PASS: gateway /health" -ForegroundColor Green
            } else {
                Write-Host "FAIL: gateway /health" -ForegroundColor Red
                $ok = $false
            }
        } catch {
            Write-Host "FAIL: gateway /health unreachable" -ForegroundColor Red
            $ok = $false
        }
        try {
            Invoke-WebRequest -Uri "http://localhost:9000/minio/health/live" -TimeoutSec 5 -UseBasicParsing | Out-Null
            Write-Host "PASS: MinIO" -ForegroundColor Green
        } catch {
            Write-Host "FAIL: MinIO unreachable" -ForegroundColor Red
            $ok = $false
        }
        try {
            Invoke-WebRequest -Uri "http://localhost:8222/healthz" -TimeoutSec 5 -UseBasicParsing | Out-Null
            Write-Host "PASS: NATS" -ForegroundColor Green
        } catch {
            Write-Host "FAIL: NATS unreachable" -ForegroundColor Red
            $ok = $false
        }
        if (-not $ok) {
            exit 1
        }
        Write-Host "All smoke checks passed." -ForegroundColor Green
    }
    "clean" {
        Invoke-DockerCompose @("down", "-v")
        $cacheDirs = @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
        foreach ($dir in $cacheDirs) {
            Get-ChildItem -Path . -Filter $dir -Recurse -Force -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    "rebuild" {
        Invoke-DockerCompose @("build", "--no-cache")
    }
    default {
        Write-Host "Unknown target: $Target" -ForegroundColor Red
        exit 1
    }
}
