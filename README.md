# Capital Sense Lead Generator — Setup

## Prerequisites

- Python 3.11
- 8GB+ RAM (16GB recommended for local AI)
- 6GB free disk space (for Llama 3.1 model)

## Step 1: Install Ollama (local AI — free forever)

macOS/Linux:
    curl -fsSL https://ollama.com/install.sh | sh

Windows:
    Download from https://ollama.com/download/windows and run the installer.

## Step 2: Download the AI model (one-time, ~4.7GB)

    ollama pull llama3.1

## Step 3: Start Ollama server

    ollama serve

Keep this terminal open. Ollama runs on http://localhost:11434

## Step 4: Install Python dependencies

    pip install -r backend/requirements.txt
    python -m spacy download en_core_web_lg
    playwright install chromium

## Step 5: Configure environment

    cp .env.example .env

Optional: add your free Hunter.io key to .env for better email enrichment.
Get it at hunter.io — sign up free, no credit card needed.
All other features work with no API keys at all.

## Step 6: Start the backend

    cd backend
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

## Step 7: Open the dashboard

Double-click frontend/index.html to open in your browser.
Or serve it:
    python -m http.server 3000 --directory frontend
    # Open http://localhost:3000

## Step 8: Run the pipeline

1. Confirm the green "Ollama connected" badge in the dashboard
2. Click "Start Pipeline"
3. Watch real-time progress — the AI model runs locally
4. Click "Download Excel" when complete

## API Keys Summary

| Service | Required? | Where to get | Cost |
|---------|-----------|-------------|------|
| Ollama  | YES       | ollama.com (install locally) | Free forever |
| Hunter.io | Optional | hunter.io (sign up free) | 25 searches/month free |
| Everything else | No | — | Free |

## Performance Notes

- On CPU only: each Ollama call takes 20-60 seconds. Full pipeline: 2-4 hours.
- On Apple Silicon (M1/M2/M3): each call takes 3-8 seconds. Full pipeline: 20-40 minutes.
- On NVIDIA GPU: each call takes 1-3 seconds. Full pipeline: 10-20 minutes.
- The dashboard shows live progress so you can monitor it.
