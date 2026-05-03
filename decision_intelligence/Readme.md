# Decision Intelligence System — Setup Guide

A multi-agent pipeline that collects competitor data, user conversations, and internal
transcripts, then synthesises them into validated product insights and feature briefs.

---

## Project Structure

```
decision_intel/
│
├── agents/
│   ├── model_connect.py            # LLM gateway (Gemini / Claude / OpenAI)
│   ├── agent1_orchestrator.py      # Data collection controller
│   ├── agent2.py                   # Problem extraction
│   ├── agent3.py                   # Research synthesis
│   └── agent4.py                   # Product brief generation
│
├── scrapers/
│   ├── company_profile_best.py     # Competitor profiler (Gemini + Google Search)
│   ├── reddit_6_working_f.py       # Reddit scraper
│   ├── youtube_scraper.py          # YouTube comment scraper
│   ├── play_store_2_working.py     # Play Store review scraper
│   ├── app_store_3_working.py      # App Store review scraper
│   └── agent1_internal_cloud.py    # Internal transcript processor
│
├── core/
│   ├── analyzer.py                 # Universal content analysis engine
│   └── pipeline.py                 # Full pipeline runner (entry point)
│
├── data/
│   ├── projects/                   # Per-project JSON docs (fallback if no MongoDB)
│   ├── results/                    # Competitor profile JSON output
│   ├── scraped/                    # Raw scraper output (per project subfolder)
│   └── signals/                    # Internal transcript signal files
│
├── tests/
│   └── test_pipeline.py            # Full test suite
│
├── .env                            # Your secrets (never commit this)
├── .env.example                    # Template — copy to .env and fill in
├── .gitignore
└── requirements.txt
```

---

## Step 1 — Clone and Create the Folder Structure

```bash
# Create the project root
mkdir decision_intel
cd decision_intel

# Create all required directories
mkdir -p agents scrapers core data/projects data/results data/scraped data/signals tests
```

---

## Step 2 — Place Each File

Copy each file to its correct location:

| File | Location |
|---|---|
| `model_connect.py` | `agents/model_connect.py` |
| `agent1_orchestrator.py` | `agents/agent1_orchestrator.py` |
| `agent2.py` | `agents/agent2.py` |
| `agent3.py` | `agents/agent3.py` |
| `agent4.py` | `agents/agent4.py` |
| `company_profile_best.py` | `scrapers/company_profile_best.py` |
| `reddit_6_working_f.py` | `scrapers/reddit_6_working_f.py` |
| `youtube_scraper.py` | `scrapers/youtube_scraper.py` |
| `play_store_2_working.py` | `scrapers/play_store_2_working.py` |
| `app_store_3_working.py` | `scrapers/app_store_3_working.py` |
| `agent1_internal_cloud.py` | `scrapers/agent1_internal_cloud.py` |
| `analyzer.py` | `core/analyzer.py` |
| `pipeline.py` | `core/pipeline.py` |
| `test_pipeline.py` | `tests/test_pipeline.py` |
| `.env.example` | `.env.example` (project root) |

---

## Step 3 — Python Environment

```bash
# Create a virtual environment (do this once)
python -m venv venv

# Activate it
# On Mac / Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install all dependencies
pip install -r requirements.txt
```

**`requirements.txt`** — create this file in the project root with the following content:

```
# LLM SDKs
google-genai>=1.0.0
anthropic>=0.25.0
openai>=1.0.0

# Data / scraping
praw>=7.7.0
google-play-scraper>=1.2.4
app-store-scraper>=0.3.5
youtube-comment-downloader>=0.1.68
requests>=2.31.0
beautifulsoup4>=4.12.0

# File processing
python-docx>=1.1.0
openpyxl>=3.1.0
xlrd>=2.0.1

# ML / classification
scikit-learn>=1.4.0
huggingface-hub>=0.22.0

# Database
pymongo>=4.6.0

# Utilities
python-dotenv>=1.0.0
```

---

## Step 4 — Environment Variables

```bash
# Copy the template
cp .env.example .env
```

Open `.env` and fill in the values:

```
GEMINI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here      # only if using Claude
OPENAI_API_KEY=your_key_here         # only if using ChatGPT
HF_TOKEN=your_huggingface_token
MONGO_URI=mongodb+srv://...          # leave blank to use local JSON fallback
MONGO_DB=decision_intel
```

**Which keys are required right now:**

| Key | Required | Notes |
|---|---|---|
| `GEMINI_API_KEY` | Yes (if using Gemini) | Default provider in `model_connect.py` |
| `HF_TOKEN` | Yes | Used by `agent1_internal_cloud.py` for transcript classification |
| `ANTHROPIC_API_KEY` | Only if you switch to Claude | Set `DEFAULT_PROVIDER = "claude"` in `model_connect.py` |
| `MONGO_URI` | No | If missing, all project docs save to `data/projects/` as JSON |

---

## Step 5 — Connect Your Scrapers to the Orchestrator

Open `agents/agent1_orchestrator.py` and find the four scraper import blocks
(around lines 80–130). Update the function names to match the actual public
functions inside your scraper files.

Example — if `reddit_6_working_f.py` exposes a function called `run_scraper`:

```python
# BEFORE
from reddit_6_working_f import scrape_reddit
result = scrape_reddit(project_name, output_dir=str(out))

# AFTER
from reddit_6_working_f import run_scraper
result = run_scraper(project_name, output_dir=str(out))
```

Do the same for YouTube, Play Store, and App Store.

---

## Step 6 — Switch the Active LLM (optional)

All agents route through `agents/model_connect.py`. To change the model for the
entire project, edit only these two lines at the top of that file:

```python
DEFAULT_PROVIDER = "gemini"           # "gemini" | "claude" | "openai"
DEFAULT_MODEL    = "gemini-2.5-flash"
```

To use Claude for one specific agent call without changing the default, pass
`provider_override`:

```python
from model_connect import model_connect
result = model_connect(prompt, provider_override="claude", model_override="default")
```

---

## Step 7 — Python Path Setup

Because agents import scrapers and core files, add the project root to your
Python path. The simplest way is a `conftest.py` (for tests) and a shell alias.

Create `conftest.py` in the project root:

```python
# conftest.py
import sys
from pathlib import Path

# Add all sub-folders to path so cross-folder imports work
root = Path(__file__).parent
for folder in ["agents", "scrapers", "core"]:
    sys.path.insert(0, str(root / folder))
```

Then in every script that needs cross-folder imports, add at the top:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scrapers"))
```

Or, simplest for daily use — run all scripts from the project root:

```bash
cd decision_intel
PYTHONPATH=agents:scrapers:core python core/pipeline.py --project Groww
```

---

## Running the Pipeline

**Full run (all agents):**
```bash
PYTHONPATH=agents:scrapers:core \
python core/pipeline.py --project Groww --domain groww.in
```

**Skip scrapers (competitor profile + internal only):**
```bash
python core/pipeline.py --project Groww --domain groww.in --skip B
```

**With internal transcript files:**
```bash
python core/pipeline.py \
  --project Groww \
  --domain groww.in \
  --internal data/raw/call1.txt data/raw/call2.md
```

**Resume from a specific agent (after agent1 already ran):**
```bash
python core/pipeline.py --project groww --start-from agent2
```

**Run only one agent:**
```bash
python core/pipeline.py --project groww --only agent3
```

**Save full output to file:**
```bash
python core/pipeline.py --project Groww --output data/results/groww_final.json
```

---

## Running the Tests

```bash
# From the project root
PYTHONPATH=agents:scrapers:core python tests/test_pipeline.py

# Run a specific test
PYTHONPATH=agents:scrapers:core python tests/test_pipeline.py --test model_connect
PYTHONPATH=agents:scrapers:core python tests/test_pipeline.py --test agent2
PYTHONPATH=agents:scrapers:core python tests/test_pipeline.py --test full_pipeline
```

---

## MongoDB Setup (optional but recommended for team use)

1. Create a free cluster at [mongodb.com/atlas](https://www.mongodb.com/atlas)
2. Create a database user with read/write access
3. Copy the connection string into `MONGO_URI` in your `.env`
4. Whitelist your IP address in Atlas Network Access

Each project is stored as a single document with this shape:

```json
{
  "project_id": "groww",
  "project_name": "Groww",
  "domain": "groww.in",
  "status": "pipeline_complete",
  "agent1": { "competitor_profile": {}, "user_conversations": {}, "internal_signals": {} },
  "agent2": { "problems": [] },
  "agent3": { "insights": [] },
  "agent4": { "briefs": [], "sprint_focus": "..." }
}
```

---

## Switching Between Models

| Task | Recommended Model | How to set |
|---|---|---|
| Development / testing | `gemini-2.0-flash` | `DEFAULT_MODEL = "gemini-2.0-flash"` |
| Production runs | `gemini-2.5-flash` | `DEFAULT_MODEL = "gemini-2.5-flash"` |
| Deep research (Agent 3/4) | `gemini-2.5-pro` | pass `model_override="pro"` in the agent call |
| Claude (alternative) | `claude-sonnet-4-6` | `DEFAULT_PROVIDER = "claude"` |

---

## Common Issues

**`ModuleNotFoundError: No module named 'model_connect'`**
Run with `PYTHONPATH=agents:scrapers:core` prefix, or add the folders to your path
as described in Step 7.

**`GEMINI_API_KEY not set`**
Make sure `.env` exists in the project root (not inside a subfolder) and you have
activated your virtual environment.

**`MongoDB unavailable — falling back to local JSON`**
This is a warning, not an error. The system works fine with local JSON.
Set `MONGO_URI` in `.env` to suppress it.

**Scraper ImportError**
The orchestrator expects specific function names from each scraper file.
Check Step 5 to align function names.

**Agent 2/3/4 says "run Agent 1 first"**
The project document status must be `agent1_done` or `agent1_partial`.
Check `data/projects/{project_id}.json` to see the current status.

---

## Team Usage (10 users)

For a team, the recommended setup is:

1. Each person works in their own branch
2. All scrapers and agents write to a shared MongoDB Atlas database
3. One shared `.env` is distributed securely (not via git) — use a password manager
   or a secrets tool like Doppler
4. Add `.env` and `data/` to `.gitignore`

`.gitignore` minimum:
```
.env
data/
venv/
__pycache__/
*.pyc
.DS_Store
```