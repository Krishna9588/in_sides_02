"""
analyzer_v2.py - Enhanced Universal Content Analyzer
Production-ready with improved platform detection, prompts, and error handling.

Usage:
    python analyzer_v2.py --input data/app_store_instagram.json
    python analyzer_v2.py --bulk data/ --mode comprehensive
    python analyzer_v2.py --input data/ --platform play_store --analyze-only

    from analyzer_v2 import analyze_file, analyze_directory
    result = analyze_file("data/app_store_instagram.json", mode="detailed")
"""

import os
import sys
import json
import time
import argparse
import logging
from typing import Optional, Dict, List, Union
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()

HF_TOKEN = os.environ.get("HF_TOKEN", "")

if not HF_TOKEN:
    logger.error("HF_TOKEN not found")
    sys.exit(1)


class EnhancedAnalyzer:
    """Enhanced analyzer with better platform detection and prompts."""

    MODES = {
        "quick": {"tokens": 500, "depth": "brief", "time": 30},
        "detailed": {"tokens": 1000, "depth": "standard", "time": 60},
        "comprehensive": {"tokens": 2000, "depth": "deep", "time": 120},
    }

    def __init__(self, mode: str = "detailed"):
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode: {mode}")
        self.mode = mode
        self.config = self.MODES[mode]

    def detect_platform(self, data: Dict) -> str:
        """Advanced platform detection with confidence scoring."""
        indicators = {
            'app_store': 0,
            'play_store': 0,
            'reddit': 0,
            'youtube': 0,
        }

        # Check extraction_metadata
        source = data.get('extraction_metadata', {}).get('source', '').lower()
        if 'apple' in source or 'itunes' in source:
            indicators['app_store'] += 3
        elif 'play' in source or 'google' in source:
            indicators['play_store'] += 3
        elif 'reddit' in source:
            indicators['reddit'] += 3
        elif 'youtube' in source:
            indicators['youtube'] += 3

        # Check extracted_data structure
        ed = data.get('extracted_data', {})

        # App Store markers
        if any(k in ed.get('metadata', {}) for k in ['trackId', 'bundleId', 'artistName']):
            indicators['app_store'] += 2
        if 'reviews' in ed and 'metadata' in ed:
            indicators['app_store'] += 1

        # Play Store markers
        if any(k in ed.get('metadata', {}) for k in ['permissions', 'installs', 'contentRating']):
            indicators['play_store'] += 2

        # Reddit markers
        if any(k in ed for k in ['subreddit', 'posts']) or 'communityName' in str(ed):
            indicators['reddit'] += 2

        # YouTube markers
        if any(k in ed for k in ['channel', 'videos']) or 'channelName' in str(ed):
            indicators['youtube'] += 2

        best_platform = max(indicators, key=indicators.get)
        confidence = indicators[best_platform]

        if confidence == 0:
            logger.warning("Could not detect platform, defaulting to generic")
            return "generic"

        logger.info(f"Detected platform: {best_platform} (confidence: {confidence}/5)")
        return best_platform

    def build_platform_prompt(self, data: Dict, platform: str) -> str:
        """Build optimized prompts per platform."""

        ed = data.get('extracted_data', {})
        depth = self.config['depth']

        # Serialize data for prompt
        if platform == 'app_store':
            meta = ed.get('metadata', {})
            reviews = ed.get('reviews', [])[:10]  # Top 10 reviews
            review_analysis = ed.get('review_analysis', {})

            reviews_summary = "\n".join([
                f"[{r['rating']}★] {r['title']}: {r['content'][:100]}"
                for r in reviews
            ])

            return f"""Analyze this Apple App Store application with {depth} analysis.

APP METADATA:
- Name: {meta.get('trackName')}
- Developer: {meta.get('artistName')}
- Category: {meta.get('primaryGenreName')}
- Rating: {meta.get('averageUserRating')}/5 ({meta.get('userRatingCount'):,} ratings)
- Version: {meta.get('version')}
- Price: {meta.get('formattedPrice')}
- Description: {meta.get('description', '')[:300]}

REVIEW ANALYSIS:
- Total Reviews Analyzed: {review_analysis.get('total_reviews')}
- Average Rating: {review_analysis.get('average_rating')}/5
- Distribution: {review_analysis.get('rating_distribution')}

SAMPLE REVIEWS:
{reviews_summary}

Provide analysis in JSON format with:
- "summary": 2-3 sentence app overview
- "strengths": list of top 5 strengths based on reviews (max 5)
- "weaknesses": list of top 5 weaknesses based on reviews (max 5)
- "sentiment": "Very Positive"/"Positive"/"Neutral"/"Negative"/"Very Negative"
- "rating_insight": insight about the rating distribution
- "monetization_model": analysis of pricing/premium features
- "key_issues": list of recurring complaints (max 5)
- "recommendation": brief recommendation for users
- "confidence": 0-100 confidence in this analysis

Return ONLY valid JSON."""

        elif platform == 'play_store':
            meta = ed.get('metadata', {})
            reviews = ed.get('reviews', [])[:10]
            review_analysis = ed.get('review_analysis', {})

            return f"""Analyze this Google Play Store application with {depth} analysis.

APP METADATA:
- Name: {meta.get('app_name')}
- Developer: {meta.get('company')}
- Category: {meta.get('category')}
- Rating: {meta.get('rating')}/5 ({meta.get('total_ratings'):,} ratings)
- Installs: {meta.get('installs')}
- Price: {meta.get('price', 'Free')}
- Permissions: {meta.get('permissions_count')} total
- Risk Level: {meta.get('high_risk_permissions', {}).get('risk_level')}

REVIEWS ANALYSIS:
- Total: {review_analysis.get('total_reviews')}
- Average: {review_analysis.get('average_rating')}/5

Provide JSON with:
- "summary": app overview
- "permissions_risk": assessment of permission risk
- "strengths": list (max 5)
- "weaknesses": list (max 5)
- "sentiment": sentiment analysis
- "security_notes": security/privacy observations
- "recommendation": brief recommendation

Return ONLY valid JSON."""

        elif platform == 'reddit':
            ed_type = ed.get('type', 'subreddit')

            if ed_type == 'subreddit':
                meta = ed.get('metadata', {})
                posts = ed.get('posts', [])[:5]

                posts_summary = "\n".join([
                    f"[{p.get('score')} upvotes, {p.get('num_comments')} comments] {p.get('title', '')[:80]}"
                    for p in posts
                ])

                return f"""Analyze this Reddit subreddit with {depth} analysis.

SUBREDDIT: r/{meta.get('subreddit')}
- Posts Analyzed: {meta.get('posts_extracted')}
- Period: Last {meta.get('months_analyzed')} month(s)

TOP POSTS:
{posts_summary}

Provide JSON with:
- "summary": subreddit overview
- "community_vibe": description of community atmosphere
- "hot_topics": current trending topics (max 5)
- "engagement_level": "Low"/"Medium"/"High"
- "content_quality": "Low"/"Medium"/"High"
- "key_themes": recurring discussion themes (max 5)
- "sentiment_distribution": sentiment breakdown
- "recommendation": is it worth joining?

Return ONLY valid JSON."""

            else:  # Post
                post = ed.get('post', {})
                comments = ed.get('comments', [])[:5]

                return f"""Analyze this Reddit post with {depth} analysis.

POST: {post.get('title')}
- Author: {post.get('author')}
- Subreddit: r/{post.get('subreddit')}
- Score: {post.get('score')}
- Comments: {len(comments)}

TOP COMMENTS SAMPLE:
{chr(10).join([f"- {c['body'][:100]}" for c in comments])}

Provide JSON with:
- "summary": post summary
- "sentiment": overall sentiment
- "discussion_quality": quality of discussion
- "key_points": main discussion points (max 5)
- "community_response": how community responded
- "controversy_level": "None"/"Low"/"Medium"/"High"

Return ONLY valid JSON."""

        else:  # Generic/YouTube
            return f"""Analyze this data with {depth} analysis.

Data: {json.dumps(ed, indent=2)[:1500]}

Provide analysis in JSON with:
- "summary": overview
- "key_insights": list (max 5)
- "sentiment": overall sentiment
- "strengths": list (max 5)
- "weaknesses": list (max 5)
- "recommendation": brief recommendation

Return ONLY valid JSON."""

    def analyze(self, data: Union[Dict, str], platform: str = "auto") -> Dict:
        """Analyze data with intelligent platform detection."""

        # Load data if file path
        if isinstance(data, str):
            try:
                with open(data, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                return {"status": "error", "error": f"Failed to load file: {e}"}

        # Detect platform
        if platform == "auto":
            platform = self.detect_platform(data)

        # Build prompt
        prompt = self.build_platform_prompt(data, platform)

        # Call API
        try:
            from huggingface_hub import InferenceClient

            client = InferenceClient(api_key=HF_TOKEN)
            start = time.time()

            response = client.chat_completion(
                model="Qwen/Qwen2.5-72B-Instruct",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.config["tokens"],
                temperature=0.1,
            )

            elapsed = (time.time() - start) * 1000
            raw = response.choices[0].message.content.strip()

            # Parse JSON
            analysis = self._parse_json(raw)

            return {
                "status": "success",
                "platform": platform,
                "mode": self.mode,
                "analysis": analysis,
                "processing_metadata": {
                    "model": "Qwen/Qwen2.5-72B-Instruct",
                    "tokens": self.config["tokens"],
                    "processing_time_ms": int(elapsed),
                    "timestamp": datetime.now().isoformat(),
                }
            }

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "platform": platform,
                "timestamp": datetime.now().isoformat(),
            }

    def _parse_json(self, text: str) -> Union[Dict, List]:
        """Extract and parse JSON from response."""
        try:
            text = text.strip()

            # Remove markdown code blocks
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    cleaned = part.strip().lstrip('json').strip()
                    if cleaned.startswith('{') or cleaned.startswith('['):
                        text = cleaned
                        break

            return json.loads(text)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON: {e}")
            return {"raw_response": text, "error": str(e)}

    def batch_analyze(
        self,
        directory: str,
        platform: str = "auto",
        filter_pattern: str = "*.json"
    ) -> Dict:
        """Batch analyze directory of JSON files."""

        files = list(Path(directory).glob(filter_pattern))
        logger.info(f"Found {len(files)} files to analyze")

        results = []
        errors = []
        total_time = 0

        for i, file_path in enumerate(files, 1):
            logger.info(f"[{i}/{len(files)}] Analyzing {file_path.name}...")

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                result = self.analyze(data, platform=platform)
                result['file'] = str(file_path)

                results.append(result)
                total_time += result.get('processing_metadata', {}).get('processing_time_ms', 0)

                status = "✓" if result['status'] == 'success' else "✗"
                print(f"  {status}")

            except Exception as e:
                errors.append({"file": str(file_path), "error": str(e)})
                print(f"  ✗ Error: {e}")

        return {
            "batch_mode": self.mode,
            "directory": directory,
            "total_files": len(files),
            "analyzed": len(results),
            "failed": len(errors),
            "success_rate": (len(results) / len(files) * 100) if files else 0,
            "total_time_ms": int(total_time),
            "results": results,
            "errors": errors,
            "completed_at": datetime.now().isoformat(),
        }


def analyze_file(filepath: str, mode: str = "detailed", platform: str = "auto") -> Dict:
    """Standalone function to analyze a single file."""
    analyzer = EnhancedAnalyzer(mode=mode)
    return analyzer.analyze(filepath, platform=platform)


def analyze_directory(directory: str, mode: str = "detailed", platform: str = "auto") -> Dict:
    """Standalone function to batch analyze directory."""
    analyzer = EnhancedAnalyzer(mode=mode)
    return analyzer.batch_analyze(directory, platform=platform)


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="Enhanced Analyzer - Intelligent Content Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single file
  python analyzer_v2.py --input data/app_store_instagram.json
  python analyzer_v2.py --input data/app_store_instagram.json --mode comprehensive
  
  # Batch directory
  python analyzer_v2.py --input data/ --batch --mode detailed
  python analyzer_v2.py --input data/ --batch --platform app_store
  
  # Output to file
  python analyzer_v2.py --input data/app_store_instagram.json --output analysis.json
        """
    )

    parser.add_argument("--input", required=True, help="Input file or directory")
    parser.add_argument("--mode", choices=["quick", "detailed", "comprehensive"], default="detailed")
    parser.add_argument("--platform", choices=["app_store", "play_store", "reddit", "youtube", "auto"], default="auto")
    parser.add_argument("--batch", action="store_true", help="Batch mode for directory")
    parser.add_argument("--output", help="Output file (optional)")

    args = parser.parse_args()

    analyzer = EnhancedAnalyzer(mode=args.mode)

    # Process
    if args.batch and os.path.isdir(args.input):
        result = analyzer.batch_analyze(args.input, platform=args.platform)
    elif os.path.isfile(args.input):
        result = analyzer.analyze(args.input, platform=args.platform)
    else:
        logger.error(f"Invalid input: {args.input}")
        return

    # Output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        logger.info(f"Saved to: {args.output}")
    else:
        print("\n" + "="*70)
        print("ANALYSIS RESULT")
        print("="*70)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()