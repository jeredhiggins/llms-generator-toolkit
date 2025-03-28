#!/bin/bash
set -e

# Install Python dependencies
pip install -r requirements.txt

# Install only Chromium (skip Firefox/WebKit)
python -m playwright install chromium

# Skip system dependencies (they're not strictly needed)
echo "Skipping system dependencies install (not required in Render)"
