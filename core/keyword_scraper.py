"""
RZ Automedata - Keyword Research / Scraper
Searches Adobe Stock via their internal Ajax API to get keyword data:
  - Total result count for a given keyword
  - Competition level (based on result count thresholds)
  - Related keywords (AI-generated via user's configured provider)
  - Opportunity scoring

Uses the public Adobe Stock Ajax endpoint with session cookie management.
"""

import re
import json
import logging
import urllib.parse
import threading
import time

logger = logging.getLogger(__name__)

# Try to import requests (should be available in the app)
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# Lazy import for AI providers (avoid circular imports)
def _get_providers():
    from core.ai_providers import PROVIDERS
    return PROVIDERS


# ─── Constants ─────────────────────────────────────────────────────────────────

# Adobe Stock asset type filters (for Ajax/Search endpoint)
ASSET_TYPE_FILTERS = {
    "all":    {},
    "photo":  {"filters[content_type:photo]": "1"},
    "vector": {"filters[content_type:illustration]": "1", "filters[content_type:zip_vector]": "1"},
    "video":  {"filters[content_type:video]": "1"},
}

ASSET_TYPE_LABELS = {
    "all":    "All Assets",
    "photo":  "Photos",
    "vector": "Vectors / Illustrations",
    "video":  "Videos",
}

# Competition thresholds (adjusted for Adobe Stock's large catalog)
COMPETITION_THRESHOLDS = {
    "very_low":  5000,
    "low":       25000,
    "medium":    100000,
    "high":      500000,
    "very_high": 1000000,
}


def _get_competition_level(total_results):
    """Determine competition level based on total results."""
    if total_results < COMPETITION_THRESHOLDS["very_low"]:
        return "VERY LOW", "🟢", "#00ff88"
    elif total_results < COMPETITION_THRESHOLDS["low"]:
        return "LOW", "🟢", "#00ff88"
    elif total_results < COMPETITION_THRESHOLDS["medium"]:
        return "MEDIUM", "🟡", "#ffaa00"
    elif total_results < COMPETITION_THRESHOLDS["high"]:
        return "HIGH", "🔴", "#ff4466"
    else:
        return "VERY HIGH", "🔴", "#ff4466"


def _get_opportunity_level(total_results):
    """Determine opportunity level (inverse of competition)."""
    if total_results < COMPETITION_THRESHOLDS["very_low"]:
        return "VERY HIGH", "🟢", "#00ff88"
    elif total_results < COMPETITION_THRESHOLDS["low"]:
        return "HIGH", "🟢", "#00ff88"
    elif total_results < COMPETITION_THRESHOLDS["medium"]:
        return "MEDIUM", "🟡", "#ffaa00"
    elif total_results < COMPETITION_THRESHOLDS["high"]:
        return "LOW", "🔴", "#ff4466"
    else:
        return "VERY LOW", "🔴", "#ff4466"


# ─── Session Management ───────────────────────────────────────────────────────

class AdobeStockSession:
    """Manages a requests session with automatic cookie refresh for Adobe Stock."""
    
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self._session = None
        self._last_init = 0
        self._request_count = 0
        self._lock = threading.Lock()
    
    def _init_session(self):
        """Initialize or refresh the session by visiting the main page."""
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        
        try:
            resp = self._session.get("https://stock.adobe.com/", timeout=15, allow_redirects=True)
            if resp.status_code == 200:
                self._last_init = time.time()
                self._request_count = 0
                logger.info("Adobe Stock session initialized successfully")
                return True
        except Exception as e:
            logger.error(f"Failed to init Adobe Stock session: {e}")
        return False
    
    def search(self, keyword, asset_type="all", timeout=15):
        """
        Search Adobe Stock and return parsed results.
        
        Returns dict with: keyword, total_results, longtail_keywords, etc.
        """
        with self._lock:
            # Re-init session if needed (every 8 requests or every 2 minutes)
            needs_refresh = (
                self._session is None or
                self._request_count >= 8 or
                (time.time() - self._last_init) > 120
            )
            if needs_refresh:
                self._init_session()
                time.sleep(0.5)
        
        if not self._session:
            raise ConnectionError("Cannot establish Adobe Stock session")
        
        # Update headers for Ajax request
        encoded_kw = urllib.parse.quote_plus(keyword.strip())
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"https://stock.adobe.com/search?k={encoded_kw}",
            "X-Requested-With": "XMLHttpRequest",
        }
        
        # Build params
        params = {
            "k": keyword.strip(),
            "limit": "1",
            "search_type": "usertyped",
        }
        
        # Add asset type filters
        type_filters = ASSET_TYPE_FILTERS.get(asset_type, {})
        params.update(type_filters)
        
        url = "https://stock.adobe.com/Ajax/Search"
        
        resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
        
        with self._lock:
            self._request_count += 1
        
        if resp.status_code == 403:
            # Session expired, try to refresh once
            logger.warning("Got 403, refreshing session...")
            with self._lock:
                self._init_session()
                time.sleep(1)
            
            resp = self._session.get(url, params=params, headers=headers, timeout=timeout)
            with self._lock:
                self._request_count += 1
        
        if resp.status_code != 200:
            raise ConnectionError(f"Adobe Stock returned HTTP {resp.status_code}")
        
        data = resp.json()
        
        return {
            "total": data.get("total", 0),
            "longtail_keywords": data.get("longtail_keywords", []),
            "num_pages": data.get("num_pages", 0),
        }


# ─── Public API ────────────────────────────────────────────────────────────────

def search_adobe_stock(keyword, asset_type="all", timeout=15):
    """
    Search Adobe Stock for a keyword and return analysis results.
    
    Args:
        keyword: Search term
        asset_type: "all", "photo", "vector", or "video"
        timeout: Request timeout in seconds
        
    Returns:
        dict with analysis data
    """
    if not HAS_REQUESTS:
        return _error_result(keyword, asset_type, "requests library not installed")
    
    encoded_kw = urllib.parse.quote_plus(keyword.strip())
    type_filters_str = ""
    for k, v in ASSET_TYPE_FILTERS.get(asset_type, {}).items():
        type_filters_str += f"&{k}={v}"
    url = f"https://stock.adobe.com/search?k={encoded_kw}{type_filters_str}"
    
    try:
        stock_session = AdobeStockSession.get_instance()
        result = stock_session.search(keyword, asset_type=asset_type, timeout=timeout)
        
        total_results = result["total"]
        longtail = result.get("longtail_keywords", [])
        
        comp_level, comp_icon, comp_color = _get_competition_level(total_results)
        opp_level, opp_icon, opp_color = _get_opportunity_level(total_results)
        
        return {
            "keyword": keyword.strip(),
            "total_results": total_results,
            "competition_level": comp_level,
            "competition_icon": comp_icon,
            "competition_color": comp_color,
            "opportunity_level": opp_level,
            "opportunity_icon": opp_icon,
            "opportunity_color": opp_color,
            "url": url,
            "asset_type": asset_type,
            "longtail_keywords": longtail,
        }
    except Exception as e:
        logger.error(f"Error searching Adobe Stock for '{keyword}': {e}")
        return _error_result(keyword, asset_type, str(e), url)


def _error_result(keyword, asset_type, error_msg, url=""):
    """Return an error result dict."""
    return {
        "keyword": keyword.strip(),
        "total_results": -1,
        "competition_level": "ERROR",
        "competition_icon": "⚠️",
        "competition_color": "#ff4466",
        "opportunity_level": "ERROR",
        "opportunity_icon": "⚠️",
        "opportunity_color": "#ff4466",
        "url": url,
        "asset_type": asset_type,
        "longtail_keywords": [],
        "error": error_msg,
    }


# ─── Batch Analysis ───────────────────────────────────────────────────────────

def analyze_keywords(keywords, asset_type="all", on_progress=None, on_keyword_done=None, 
                     stop_event=None, max_workers=1):
    """
    Analyze a list of keywords by searching Adobe Stock for each.
    Uses sequential processing with delays to avoid rate limiting.
    """
    results = []
    total = len(keywords)
    
    for i, kw in enumerate(keywords):
        if stop_event and stop_event.is_set():
            break
        
        result = search_adobe_stock(kw.strip(), asset_type=asset_type)
        results.append(result)
        
        if on_keyword_done:
            on_keyword_done(result)
        if on_progress:
            on_progress(i + 1, total)
        
        # Small delay between requests to avoid bot detection
        if i < total - 1:
            time.sleep(0.8)
    
    # Sort by opportunity (lowest results = highest opportunity)
    results.sort(key=lambda x: x.get("total_results", 0))
    
    return results


# ─── AI-Powered Related Keywords ──────────────────────────────────────────────

def generate_related_keywords_ai(keyword, asset_type="all", provider_name=None,
                                   model=None, api_key=None, stop_event=None,
                                   on_progress=None, on_keyword_done=None):
    """
    Use AI to generate contextually relevant related keywords for stock photography.
    Then look up each keyword's result count on Adobe Stock.
    
    Args:
        keyword: The original search keyword
        asset_type: "all", "photo", "vector", or "video"
        provider_name: AI provider name (e.g. "Groq", "OpenRouter")
        model: Model identifier
        api_key: API key for the provider
        stop_event: Threading event to stop early
        on_progress: Callback (current, total) for progress updates
        on_keyword_done: Callback (result_dict) when each keyword is analyzed
        
    Returns:
        list of result dicts (same format as search_adobe_stock)
    """
    if not provider_name or not model or not api_key:
        logger.warning("No AI provider configured for related keywords")
        return []
    
    if not HAS_REQUESTS:
        return []
    
    # Get provider config
    providers = _get_providers()
    provider = providers.get(provider_name)
    if not provider:
        logger.error(f"Unknown provider: {provider_name}")
        return []
    
    asset_label = ASSET_TYPE_LABELS.get(asset_type, "stock assets")
    
    # Build AI prompt for generating related keywords
    system_prompt = (
        "You are an expert Adobe Stock keyword researcher and SEO specialist. "
        "Your job is to suggest highly relevant, contextually related keywords "
        "that a stock photographer or designer would use on Adobe Stock. "
        "Focus on keywords that are semantically related, commercially valuable, "
        "and commonly searched by buyers on stock platforms."
    )
    
    user_prompt = (
        f'I\'m researching the keyword "{keyword}" for {asset_label} on Adobe Stock.\n\n'
        f'Generate exactly 15 related keywords/phrases that are:\n'
        f'- Semantically related and contextually relevant to "{keyword}"\n'
        f'- Common search terms stock photo/video buyers would actually use\n'
        f'- A mix of broader and more specific variations\n'
        f'- Commercially valuable for stock contributors\n'
        f'- NOT just the original keyword + generic modifier (like "background" or "texture")\n\n'
        f'Think about:\n'
        f'- What else would someone searching for "{keyword}" also look for?\n'
        f'- What are related concepts, scenes, or themes?\n'
        f'- What similar but different keywords target the same audience?\n\n'
        f'RESPOND WITH ONLY a JSON array of strings, nothing else:\n'
        f'["keyword 1", "keyword 2", "keyword 3", ...]'
    )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # OpenRouter requires these headers for authentication
    if provider_name == "OpenRouter":
        headers["HTTP-Referer"] = "https://rz-automedata.app"
        headers["X-Title"] = "RZ Automedata"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7
    }
    
    try:
        url = provider["base_url"]
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"AI API error ({response.status_code}): {response.text[:200]}")
            return []
        
        resp_json = response.json()
        content = resp_json["choices"][0]["message"]["content"].strip()
        
        # Parse JSON array from response
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            content = json_match.group(0)
        
        related_keywords = json.loads(content)
        
        if not isinstance(related_keywords, list):
            logger.error(f"AI returned non-list: {type(related_keywords)}")
            return []
        
        # Clean up and limit
        related_keywords = [str(kw).strip() for kw in related_keywords if str(kw).strip()]
        related_keywords = related_keywords[:15]
        
        logger.info(f"AI generated {len(related_keywords)} related keywords for '{keyword}'")
        
        # Now look up each keyword on Adobe Stock
        results = []
        total = len(related_keywords)
        for i, kw in enumerate(related_keywords):
            if stop_event and stop_event.is_set():
                break
            
            result = search_adobe_stock(kw, asset_type=asset_type)
            results.append(result)
            
            if on_keyword_done:
                on_keyword_done(result)
            if on_progress:
                on_progress(i + 1, total)
            
            time.sleep(0.8)  # Rate limit
        
        # Sort by opportunity (lowest results first)
        results.sort(key=lambda x: x.get("total_results", 0))
        
        return results
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"Error generating AI related keywords: {e}")
        return []


def format_number(num):
    """Format a number with commas for display."""
    if num < 0:
        return "N/A"
    return f"{num:,}"
