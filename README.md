# disc-detect

Two pieces live in this repo:

- **[`indexer/`](indexer/)** — crop a photo of CD jewel cases, reverse-image-search the covers, and export a Discogs track CSV (`python -m indexer`)
- **[`app/`](app/)** — React + FastAPI catalog for loading those discs into a Pioneer PD-F1009 and playing them

## Quick start

Indexer (from repo root):

```bash
pip install -r indexer/requirements.txt
python -m indexer align indexer/image1.jpg
```

App (catalog / player):

```bash
pip install -r requirements.txt
cd app/web && npm install && cd ../..
python -m app          # API on :8000
cd app/web && npm run dev   # UI on :5173
```

Or containerize the app: `docker compose up --build` → http://127.0.0.1:8000
