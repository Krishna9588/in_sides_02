"""
app_store_scraper.py - Advanced Apple App Store Scraper
Uses app-store-scraper library for comprehensive app data extraction.

Installation:
    # Install the app-store-scraper library
    pip install app-store-scraper

    # Optional: For HF analysis integration
    pip install huggingface-hub python-dotenv

Usage:
    python app_store_scraper.py -u "Instagram"                    # Interactive
    python app_store_scraper.py -u 389801252                       # Direct ID
    python app_store_scraper.py -u "Instagram" --reviews 200       # Custom reviews
    python app_store_scraper.py -u "Instagram" --deep-extract      # All metadata
    python app_store_scraper.py -u "Instagram" --screenshots       # Media extraction
    python app_store_scraper.py -u "Instagram" --analyze           # With HF analysis
    python app_store_scraper.py --bulk apps.txt --country US       # Bulk processing
    python app_store_scraper.py --analyze-only data.json           # Analyze pre-scraped

    from app_store_scraper import app_store_scraper
    result = app_store_scraper("Instagram", reviews=100, analyze=True)


Programmatic Usage

from app_store_scraper import app_store_scraper

# Simple usage
result = app_store_scraper("Instagram")

# Full featured
result = app_store_scraper(
    input_str="Instagram",
    reviews=150,
    screenshots=True,
    version_history=True,
    analyze=True,
    country="us",
    output="data"
)

# Bulk processing
apps = ["Instagram", "TikTok", "WhatsApp"]
for app in apps:
    result = app_store_scraper(app, output="data")


"""