#!/bin/bash
# render-build.sh
set -e

echo "-----> Installing Python dependencies"
pip install -r requirements.txt

echo "-----> Installing Playwright browsers"
python -m playwright install

echo "-----> Installing system dependencies for Playwright"
python -m playwright install-deps
