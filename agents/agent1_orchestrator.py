import os
import json
import asyncio
from datetime import datetime
from typing import Dict, Any

# Import existing scrapers
from agent_1.company_profile_best import run_research_task
from agent_1.play_store_2_working import play_store
from agent_1.app_store_3_working import app_store
from agent_1.reddit_6_working_f import reddit
from agent_1.youtube_scraper import youtube_scraper
from agent_1.agent1_internal_cloud import agent1_internal_batch, agent1_internal

# Database Configuration (Mocking MongoDB for now)
DB_FOLDER = "database_mock"


async def run_scraper_safe(scraper_func, *args, **kwargs) -> Any:
    """Wraps synchronous scrapers in an async thread and catches any errors to prevent crashes."""
    try:
        return await asyncio.to_thread(scraper_func, *args, **kwargs)
    except Exception as e:
        print(f"[ERROR] Scraper {scraper_func.__name__} failed: {e}")
        return {"status": "error", "error": str(e)}


async def orchestrate_agent_1(payload: Dict[str, Any]) -> str:
    """
    Main entry point for the frontend.
    Accepts a JSON payload, triggers scrapers concurrently, and saves to DB.
    """
    project_name = payload.get("project_name")
    if not project_name:
        raise ValueError("Error: 'project_name' is mandatory.")

    print(f"\n🚀 [AGENT 1] Starting Intelligence Gathering for: {project_name.upper()}\n")

    # 1. Dynamically build tasks based on frontend payload
    task_map = {}  # Dictionary to keep track of which task belongs to which platform

    # --- COMPANY PROFILE (Always runs using project_name, domain is optional) ---
    domain = payload.get("domain")
    task_map["company_profile"] = run_scraper_safe(
        run_research_task,
        company_input=project_name,
        company_domain=domain
    )

    # --- PLAY STORE ---
    if "play_store" in payload:
        ps_data = payload["play_store"]
        task_map["play_store"] = run_scraper_safe(
            play_store,
            input_str=ps_data.get("link_or_id"),
            reviews=ps_data.get("reviews_count", 100),
            interactive=False, verbose=False
        )

    # --- APP STORE ---
    if "app_store" in payload:
        as_data = payload["app_store"]
        task_map["app_store"] = run_scraper_safe(
            app_store,
            input_str=as_data.get("link_or_id"),
            reviews=as_data.get("reviews_count", 100),
            interactive=False, verbose=False
        )

    # --- REDDIT ---
    if "reddit" in payload:
        rd_data = payload["reddit"]
        task_map["reddit"] = run_scraper_safe(
            reddit,
            user_input=rd_data.get("query_or_subreddit"),
            mode=rd_data.get("mode", "search"),  # 'search', 'subreddit', 'user'
            limit=rd_data.get("limit", 10),
            scrape_comments=True, verbose=False
        )

    # --- YOUTUBE ---
    if "youtube" in payload:
        yt_data = payload["youtube"]
        task_map["youtube"] = run_scraper_safe(
            youtube_scraper,
            mode=yt_data.get("mode"),  # 'video', 'channel', 'search'
            video_url=yt_data.get("video_url"),
            channel_url=yt_data.get("channel_url"),
            query=yt_data.get("query"),
            count=yt_data.get("count", 5)
        )

    # --- INTERNAL TRANSCRIPTS ---
    if "transcripts" in payload:
        ts_data = payload["transcripts"]
        input_path = ts_data.get("input_path")
        if input_path and os.path.exists(input_path):
            if os.path.isdir(input_path):
                task_map["internal_transcripts"] = run_scraper_safe(agent1_internal_batch, input_dir=input_path)
            else:
                task_map["internal_transcripts"] = run_scraper_safe(agent1_internal, input_path=input_path)
        else:
            print(f"[WARNING] Transcript path not found: {input_path}")

    # 2. Execute all dispatched tasks concurrently
    print(f"-> Dispatching {len(task_map)} concurrent scraping tasks...")

    # gather() preserves order. We use task_map.keys() to map results back properly.
    keys = list(task_map.keys())
    tasks = list(task_map.values())

    results_list = await asyncio.gather(*tasks)

    # Zip the keys and results back into a dictionary
    scraped_data = dict(zip(keys, results_list))

    # 3. Structure the Final Document for the Database
    print("\n-> Structuring Database Document...")
    final_document = {
        "project_name": project_name,
        "domain": domain,
        "ingestion_date": datetime.now().isoformat(),
        "data_sources": scraped_data,  # Contains all the scraped raw data
        "processing_status": {
            "agent2_insights_extracted": False,
            "agent3_synthesis_done": False,
            "agent4_product_brief_done": False
        },
        "agent2_output": {},
        "agent3_output": {},
        "agent4_output": {}
    }

    # 4. Save to our "Database"
    # When migrating to MongoDB, you would do: db.projects.insert_one(final_document)
    os.makedirs(os.path.join(DB_FOLDER, project_name), exist_ok=True)
    db_filepath = os.path.join(DB_FOLDER, project_name, "db_document.json")

    with open(db_filepath, "w", encoding="utf-8") as f:
        json.dump(final_document, f, indent=4, ensure_ascii=False)

    print(f"✅ [SUCCESS] Agent 1 data saved to Database: {db_filepath}")
    return db_filepath


# ==========================================
# HOW TO TEST (Interactive CLI)
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("  AGENT 1: INTELLIGENCE GATHERING SETUP")
    print("=" * 60)

    # Mandatory
    project_name = input("Enter Project/Company Name (Mandatory): ").strip()
    while not project_name:
        project_name = input("Project Name is required. Please enter: ").strip()

    # Optional
    domain = input(f"Enter Domain for {project_name} (Optional, press Enter to skip): ").strip()

    play_store_id = input("Enter Play Store App ID (e.g., com.nextbillion.groww) (Optional): ").strip()
    app_store_id = input("Enter App Store App ID (e.g., 1434524388) (Optional): ").strip()

    reddit_query = input("Enter Reddit Search Query or Subreddit (Optional): ").strip()
    youtube_query = input("Enter YouTube Search Query (Optional): ").strip()

    transcript_path = input("Enter path to Internal Transcripts folder/file (Optional): ").strip()

    # Build Payload
    frontend_payload = {"project_name": project_name}
    if domain: frontend_payload["domain"] = domain
    if play_store_id: frontend_payload["play_store"] = {"link_or_id": play_store_id, "reviews_count": 50}
    if app_store_id: frontend_payload["app_store"] = {"link_or_id": app_store_id, "reviews_count": 50}
    if reddit_query: frontend_payload["reddit"] = {"query_or_subreddit": reddit_query, "mode": "search", "limit": 5}
    if youtube_query: frontend_payload["youtube"] = {"mode": "search", "query": youtube_query, "count": 3}
    if transcript_path: frontend_payload["transcripts"] = {"input_path": transcript_path}

    print("\n[INFO] Launching Orchestrator with payload:")
    print(json.dumps(frontend_payload, indent=2))

    # Run the async orchestrator
    asyncio.run(orchestrate_agent_1(frontend_payload))