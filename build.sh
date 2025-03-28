#!/bin/bash
set -e

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Clean existing and reinstall Chromium
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/.cache/ms-playwright
mkdir -p $PLAYWRIGHT_BROWSERS_PATH
rm -rf $PLAYWRIGHT_BROWSERS_PATH/chromium-*  # Remove any existing chromium installs
python -m playwright install --force chromium
