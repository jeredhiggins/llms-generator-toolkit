#!/bin/bash
set -ex  # Enable debugging

# Create virtual env
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Chromium with explicit cache path
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/.cache/ms-playwright
mkdir -p $PLAYWRIGHT_BROWSERS_PATH

# Force install and verify
python -m playwright install --force chromium
python -m playwright install-deps  # System dependencies
