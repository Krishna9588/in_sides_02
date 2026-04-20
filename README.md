# Founder Intelligence System (v1)

An AI-powered decision intelligence platform designed to replace manual research. It automates the collection and analysis of transcripts, competitor movements, regulatory updates, and user sentiment to provide founders with actionable product insights.

## Overview

The **Founder Intelligence System** acts as a virtual research intern and market analyst. It closes the loop between raw data and strategic decisions:
**Data Ingestion → Signal Extraction → Synthesis → Product Ideas → Founder Copilot.**

---

## Key Features

### Transcript Insights
- **Input:** Audio or Text (Sales calls, Investor meetings, Product discussions).
- **Processing:** Extracts key problems, decisions, and action items.
- **Output:** Structured summaries and next-step recommendations stored for RAG.

### Competitor & Market Research
- **Tracking:** Monitors 5-8 key competitors (e.g., Liquide, StockEdge, Univest).
- **Intelligence:** Extracts tech stacks, funding, revenue models, and strategic moves.
- **Gap Analysis:** Identifies what competitors have vs. what users actually want.

### ️ Regulatory & News Monitoring (SEBI/RBI)
- **Automated Scraping:** Real-time monitoring of SEBI circulars and RBI notifications.
- **Impact Scoring:** Uses Gemini Pro to rate how new regulations affect your business model.
- **TL;DR:** 3-sentence executive summaries for regulatory compliance.

### Review & Sentiment Analysis
- **Sources:** Play Store, App Store, Reddit, G2, and Trustpilot.
- **Signal Extraction:** Filters through the noise to find specific user "pain points" and feature requests.

### Founder Copilot (RAG Chatbot)
- **Context-Aware:** Answers questions like "What should we build next?" or "What are competitors doing differently?"
- **Data-Backed:** Prioritizes results from the internal database (Supabase) before checking the web.

---

## Architecture: The 5-Agent Pipeline

1.  **Research Ingestion Agent:** Collects raw signals from scrapers, transcripts, and notes.
2.  **Insight Extraction Agent:** Converts raw signals into structured problems and patterns.
3.  **Research Synthesis Agent:** Identifies root causes and higher-level market hypotheses.
4.  **Product Brief Agent:** Translates insights into buildable feature ideas and user flows.
5.  **Founder Copilot:** A RAG-powered interface for natural language querying.

---

## Tech Stack

- **Backend:** Python (FastAPI)
- **AI/LLM:** Gemini Pro (Google AI Studio API)
- **Orchestration:** LangChain / Agentic Workflows
- **Database:** Supabase (Postgres + `pgvector`)
- **Embeddings:** Hugging Face (`all-MiniLM-L6-v2`)
- **Scraping:** Apify, Crawl4AI, BeautifulSoup
- **Infrastructure:** Hostinger (Backend), Vercel (Frontend)

---

## Project Structure

```text
├── competitor_tracking/  # Data related to market competitors
├── data/                 # Raw and processed datasets
├── transcript/           # Meeting and call transcripts
├── user_conversations/   # Scraped reviews and forum data
├── requirements.txt      # Python dependencies
└── instructions_details.md # Project design and strategy doc
```

---

## Getting Started

### Prerequisites
- Python 3.10+
- Supabase Account
- Google AI Studio (Gemini) API Key

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-repo/final_insights.git
   cd final_insights
   ```

2. **Set up virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment:**
   Create a `.env` file in the root directory:
   ```env
   SUPABASE_URL=your_supabase_url
   SUPABASE_KEY=your_supabase_anon_key
   GOOGLE_API_KEY=your_gemini_api_key
   ```

5. **Run the API:**
   ```bash
   uvicorn main:app --reload
   ```
   

### website we can use for scraping 

1. Google Play Store: https://pypi.org/project/google-play-scraper/
2. Google Play Store: https://github.com/facundoolano/google-play-scraper
3. App Store: https://pypi.org/project/app-store-scraper/
4. Scrape Reddit: https://pypi.org/project/mcp-reddit/
5. Youtube Videos: https://pypi.org/project/mcp-youtube/
6. Youtube Videos: https://pypi.org/project/download-youtube-subtitle/
---