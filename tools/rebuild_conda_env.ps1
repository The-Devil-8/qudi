<#
.SYNOPSIS
Removes the existing 'qudi' conda environment and recreates it from a YAML file.

.DESCRIPTION
This script simplifies the process of testing new dependencies and recreating the Qudi
environment from scratch. It forces the removal of the old environment and installs 
fresh dependencies based on the specified conda environment file.

.PARAMETER EnvFile
The path to the conda environment YAML file to use. Defaults to the new Python 3.9 file.

.EXAMPLE
.\rebuild_conda_env.ps1
Rebuilds the environment using the default Python 3.9 config.

.EXAMPLE
.\rebuild_conda_env.ps1 -EnvFile "conda-env-win10-64bit-qt5.yml"
Rebuilds using the legacy Python 3.6 config.
#>

param (
    [string]$EnvFile = "conda-env-win10-64bit-qt5-py39.yml"
)

# Ensure the script stops on errors
$ErrorActionPreference = "Stop"

# Check if conda is available in the current context
if (-not (Get-Command "conda" -ErrorAction SilentlyContinue)) {
    Write-Host "Error: 'conda' command not found." -ForegroundColor Red
    Write-Host "Please ensure you run this script from an Anaconda Prompt, or that conda is in your system PATH." -ForegroundColor Yellow
    exit 1
}

$EnvPath = Join-Path -Path $PSScriptRoot -ChildPath $EnvFile

if (-not (Test-Path $EnvPath)) {
    Write-Host "Error: Environment file not found at $EnvPath" -ForegroundColor Red
    exit 1
}

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host " Qudi Conda Environment Rebuilder" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "Target Environment File: $EnvFile"
Write-Host "This will completely remove the 'qudi' environment and reinstall it." -ForegroundColor Yellow
Write-Host ""

# 1. Deactivate current environment (if 'qudi' is currently active, we cannot remove it)
# Note: Conda deactivate in scripts can be tricky, so we warn the user instead.
if ($env:CONDA_DEFAULT_ENV -eq "qudi") {
    Write-Host "WARNING: The 'qudi' environment is currently active." -ForegroundColor Red
    Write-Host "Please run 'conda deactivate' before running this script." -ForegroundColor Red
    exit 1
}

# 2. Remove the existing environment
Write-Host "Step 1/2: Removing existing 'qudi' environment..." -ForegroundColor Green
conda env remove --name qudi --yes

# 3. Recreate the environment
Write-Host "Step 2/2: Creating new 'qudi' environment from $EnvFile..." -ForegroundColor Green
conda env create -f $EnvPath

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host " Done!" -ForegroundColor Green
Write-Host " You can now activate the environment with: " -ForegroundColor White
Write-Host " > conda activate qudi" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan
