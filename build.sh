#!/bin/bash
set -e

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright (Chromium only)
python -m playwright install chromium
