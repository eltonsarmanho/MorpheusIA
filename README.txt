morpheus-ia/
├── index.html
├── css/
│   └── style.css
├── js/
│   ├── i18n.js
│   └── main.js
└── data/
    ├── content-pt.json
    └── content-en.json

Running the chatbot backend
============================

The chatbot backend is a FastAPI service under backend/ (full spec/design in
.specs/features/initial-support-chatbot/). It reads configuration from a
.env file at the project root (see backend/app/core/config.py).

Required environment variables (.env at the project root):
  MARITALK_API_KEY   - Maritaca AI API key
  MARITALK_API_BASE  - Maritaca AI API base URL (e.g. https://chat.maritaca.ai/api)
  MARITALK_MODEL     - model name (e.g. sabiazinho-4)
  ADMIN_API_TOKEN    - bearer token required by GET /api/leads

Optional environment variables:
  WHATSAPP_NUMBER    - number used to build the WhatsApp handoff wa.me link
                       (defaults to the placeholder 5500000000000)
  ALLOWED_ORIGINS    - comma-separated list of origins allowed by CORS
                       (defaults to localhost:8000/5500 and their 127.0.0.1
                       equivalents)

The SQLite database file is created automatically at backend/data/app.db.

Option 1 - Docker Compose:
  docker compose up --build
  The backend listens on http://localhost:8000. backend/data/ is mounted
  into the container so the SQLite file persists across restarts.

Option 2 - Bare uvicorn (no Docker):
  cd backend
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  uvicorn app.main:app --reload --port 8000