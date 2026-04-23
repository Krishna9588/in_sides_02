"""
YouTube & Reddit Analysis API - Free Version (Agent 3 Free)
Apify scraper + HuggingFace AI — no Gemini, no YouTube/Reddit API key
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List

from analyzer import analyze_video, analyze_channel, analyze_reddit_post, analyze_subreddit, check_new_posts, analyze_app

app = FastAPI(
    title="YouTube & Reddit Analysis API (Free)",
    description="Agent 3 Free: YouTube + Reddit analysis using Apify scraper + HuggingFace AI. No Gemini or platform API keys needed.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────

class VideoRequest(BaseModel):
    url: str
    class Config:
        json_schema_extra = {"example": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}}


class ChannelRequest(BaseModel):
    url: str
    max_videos: int = 10
    class Config:
        json_schema_extra = {"example": {"url": "https://www.youtube.com/@mkbhd", "max_videos": 10}}


class VideoAnalysis(BaseModel):
    video_id: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    channel: Optional[str] = None
    upload_date: Optional[str] = None
    duration: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    thumbnail: Optional[str] = None
    tags: Optional[List[str]] = None
    summary: Optional[str] = None
    main_topics: Optional[List[str]] = None
    sentiment: Optional[str] = None
    target_audience: Optional[str] = None
    key_insights: Optional[List[str]] = None
    negative_points: Optional[List[str]] = None
    content_type: Optional[str] = None
    call_to_action: Optional[str] = None
    transcript_available: Optional[bool] = None


class PerVideoSummary(BaseModel):
    video_id: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    upload_date: Optional[str] = None
    duration: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    summary: Optional[str] = None
    main_topics: Optional[List[str]] = None
    sentiment: Optional[str] = None
    content_type: Optional[str] = None


class ChannelAnalysis(BaseModel):
    channel: Optional[str] = None
    channel_url: Optional[str] = None
    videos_analyzed: Optional[int] = None
    date_range: Optional[dict] = None
    channel_summary: Optional[str] = None
    content_themes: Optional[List[str]] = None
    posting_pattern: Optional[str] = None
    audience_type: Optional[str] = None
    content_style: Optional[str] = None
    top_performing_topics: Optional[List[str]] = None
    videos: Optional[List[PerVideoSummary]] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    return {
        "status": "ok",
        "apify_token_set": bool(os.environ.get("APIFY_TOKEN", "")),
        "hf_token_set": bool(os.environ.get("HF_TOKEN", "")),
    }


@app.post("/analyze/video", response_model=VideoAnalysis, tags=["Analysis"])
def video_analysis(req: VideoRequest):
    """Analyze a single YouTube video using Apify + HuggingFace."""
    try:
        return analyze_video(req.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze/channel", response_model=ChannelAnalysis, tags=["Analysis"])
def channel_analysis(req: ChannelRequest):
    """Analyze a YouTube channel (up to 20 videos) using Apify + HuggingFace."""
    try:
        return analyze_channel(req.url, max_videos=min(req.max_videos, 20))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RedditRequest(BaseModel):
    url: str
    class Config:
        json_schema_extra = {
            "example": {"url": "https://www.reddit.com/r/india/comments/xyz/some_post/"}
        }


class RedditAnalysis(BaseModel):
    post_url: Optional[str] = None
    title: Optional[str] = None
    subreddit: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None
    score: Optional[int] = None
    upvote_ratio: Optional[float] = None
    num_comments: Optional[int] = None
    flair: Optional[str] = None
    body_preview: Optional[str] = None
    top_comments_scraped: Optional[int] = None
    summary: Optional[str] = None
    main_topics: Optional[List[str]] = None
    overall_sentiment: Optional[str] = None
    community_sentiment: Optional[str] = None
    key_opinions: Optional[List[str]] = None
    negative_points: Optional[List[str]] = None
    post_type: Optional[str] = None
    controversy_level: Optional[str] = None
    key_takeaway: Optional[str] = None


@app.post("/analyze/reddit", response_model=RedditAnalysis, tags=["Analysis"])
def reddit_analysis(req: RedditRequest):
    """
    Analyze a Reddit post and its comments.

    Returns: post metadata (score, upvote ratio, comment count, flair),
    AI-generated summary, topics, sentiment of post + community,
    key opinions from comments, post type, controversy level, and key takeaway.
    """
    try:
        return analyze_reddit_post(req.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class SubredditRequest(BaseModel):
    url: str
    max_posts: int = 20
    class Config:
        json_schema_extra = {
            "example": {"url": "https://www.reddit.com/r/IndiaInvestments/", "max_posts": 20}
        }


class PostBrief(BaseModel):
    post_id: Optional[str] = None
    post_url: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[str] = None
    score: Optional[int] = None
    upvote_ratio: Optional[float] = None
    num_comments: Optional[int] = None
    flair: Optional[str] = None
    summary: Optional[str] = None
    main_topics: Optional[List[str]] = None
    overall_sentiment: Optional[str] = None
    community_sentiment: Optional[str] = None
    negative_points: Optional[List[str]] = None
    post_type: Optional[str] = None
    controversy_level: Optional[str] = None


class SubredditAnalysis(BaseModel):
    subreddit: Optional[str] = None
    subreddit_url: Optional[str] = None
    posts_analyzed: Optional[int] = None
    date_range: Optional[dict] = None
    subreddit_summary: Optional[str] = None
    hot_topics: Optional[List[str]] = None
    dominant_sentiment: Optional[str] = None
    common_post_types: Optional[List[str]] = None
    notable_trends: Optional[List[str]] = None
    posts: Optional[List[PostBrief]] = None


class MonitorResponse(BaseModel):
    subreddit: Optional[str] = None
    new_posts_found: Optional[int] = None
    new_posts: Optional[List[PostBrief]] = None
    tip: Optional[str] = None


@app.post("/analyze/subreddit", response_model=SubredditAnalysis, tags=["Reddit"])
def subreddit_analysis(req: SubredditRequest):
    """
    Analyze the last N posts from a subreddit (default 20).

    Returns: subreddit mood summary, hot topics, dominant sentiment, trends,
    and per-post breakdown with sentiment, topics, and controversy level.

    Also initializes the live monitor — call /monitor/subreddit afterwards
    to track new posts.
    """
    try:
        return analyze_subreddit(req.url, max_posts=min(req.max_posts, 20))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/monitor/subreddit", response_model=MonitorResponse, tags=["Reddit"])
def monitor_subreddit(req: SubredditRequest):
    """
    Check for new posts in a subreddit since the last call.

    Call this every 5-10 minutes to get live updates.
    New posts are automatically analyzed and returned.
    First call /analyze/subreddit to initialize, then poll this endpoint.
    """
    try:
        return check_new_posts(req.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AppRequest(BaseModel):
    input: str
    store: str = "auto"  # "play", "appstore", or "auto"

    class Config:
        json_schema_extra = {
            "example": {
                "input": "https://play.google.com/store/apps/details?id=com.nextbillion.groww",
                "store": "auto"
            }
        }


class AppAnalysis(BaseModel):
    store: Optional[str] = None
    app_id: Optional[str] = None
    app_name: Optional[str] = None
    company: Optional[str] = None
    developer_email: Optional[str] = None
    developer_website: Optional[str] = None
    play_store_url: Optional[str] = None
    app_store_url: Optional[str] = None
    icon: Optional[str] = None
    header_image: Optional[str] = None
    screenshots: Optional[List[str]] = None
    genre: Optional[str] = None
    rating: Optional[float] = None
    total_ratings: Optional[int] = None
    total_reviews: Optional[int] = None
    installs: Optional[str] = None
    real_installs: Optional[int] = None
    file_size_mb: Optional[float] = None
    price: Optional[str] = None
    content_rating: Optional[str] = None
    version: Optional[str] = None
    released: Optional[str] = None
    last_updated: Optional[str] = None
    rating_breakdown: Optional[dict] = None
    description_preview: Optional[str] = None
    recent_changes: Optional[str] = None
    reviews_scraped: Optional[int] = None
    negative_reviews_count: Optional[int] = None
    negative_reviews: Optional[List[dict]] = None
    all_reviews_by_rating: Optional[dict] = None
    summary: Optional[str] = None
    key_features: Optional[List[str]] = None
    target_audience: Optional[str] = None
    overall_sentiment: Optional[str] = None
    top_complaints: Optional[List[str]] = None
    top_praises: Optional[List[str]] = None
    competitive_position: Optional[str] = None
    recent_issues: Optional[List[str]] = None


@app.post("/analyze/app", response_model=AppAnalysis, tags=["App Store"])
def app_analysis(req: AppRequest):
    """
    Analyze an app from Google Play Store or Apple App Store.

    Accepts:
    - Play Store URL: https://play.google.com/store/apps/details?id=com.example.app
    - App Store URL: https://apps.apple.com/in/app/name/id123456789
    - Package name: com.example.app (Play Store)
    - App ID: 123456789 (App Store)

    Returns: app metadata, ratings, installs, screenshots, description,
    AI-generated summary, key features, top complaints, top praises, sentiment.
    """
    try:
        return analyze_app(req.input, store=req.store)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
