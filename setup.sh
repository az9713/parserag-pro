#!/usr/bin/env bash
set -e

python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "Created .env — open it and add your GOOGLE_API_KEY before starting the server."
fi

echo ""
echo "Setup complete. To start the server:"
echo "  source .venv/Scripts/activate && python app.py"
