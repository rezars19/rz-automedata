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


# ─── Trending Keywords ────────────────────────────────────────────────────────

# Trending seed terms — keep minimal to avoid rate limiting (3 per category)
_TRENDING_SEEDS = {
    "photo": ["trending", "lifestyle", "nature"],
    "vector": ["icon", "background", "illustration"],
    "video": ["cinematic", "aerial", "nature"],
}


def _safe_search(session, keyword, asset_type, timeout=12):
    """Search with retry on 403 — waits and retries once."""
    try:
        result = session.search(keyword, asset_type=asset_type, timeout=timeout)
        return result
    except ConnectionError as e:
        if "403" in str(e):
            logger.info(f"Got 403 for '{keyword}', waiting 4s and retrying...")
            time.sleep(4)
            # Force fresh session
            with session._lock:
                session._init_session()
                time.sleep(1)
            try:
                return session.search(keyword, asset_type=asset_type, timeout=timeout)
            except Exception:
                return None
        return None
    except Exception:
        return None


def fetch_trending_keywords(on_progress=None, stop_event=None):
    """
    Fetch trending/popular keywords from Adobe Stock for each asset type.
    
    Uses a conservative approach with long delays to avoid 403 rate limiting.
    Searches 3 seed terms per category, collects longtail suggestions,
    then looks up the top candidates.
    
    Returns:
        dict: {"photo": [...], "vector": [...], "video": [...]}
              Each list contains dicts with: keyword, total_results, etc.
    """
    if not HAS_REQUESTS:
        logger.error("requests library not available")
        return {"photo": [], "vector": [], "video": []}

    session = AdobeStockSession.get_instance()
    asset_types = ["photo", "vector", "video"]

    # Force fresh session at start
    with session._lock:
        session._init_session()
    time.sleep(1)

    total_steps = sum(len(_TRENDING_SEEDS[t]) for t in asset_types)
    current_step = 0

    final = {"photo": [], "vector": [], "video": []}

    for asset_type in asset_types:
        if stop_event and stop_event.is_set():
            break

        seeds = _TRENDING_SEEDS[asset_type]
        collected_keywords = []
        seen = set()

        # Phase 1: Collect longtail keywords from seeds
        for seed in seeds:
            if stop_event and stop_event.is_set():
                break

            current_step += 1
            if on_progress:
                on_progress(current_step, total_steps, asset_type)

            result = _safe_search(session, seed, asset_type)
            if result:
                longtails = result.get("longtail_keywords", [])
                for lt in longtails:
                    kw_text = ""
                    if isinstance(lt, dict):
                        kw_text = lt.get("text", lt.get("keyword", "")).strip().lower()
                    elif isinstance(lt, str):
                        kw_text = lt.strip().lower()

                    if kw_text and len(kw_text) >= 3 and kw_text not in seen:
                        seen.add(kw_text)
                        collected_keywords.append(kw_text)

            # Generous delay between requests to avoid 403
            time.sleep(2)

        # Phase 2: Look up result counts for top 5 candidates only
        candidates = collected_keywords[:5]

        for kw in candidates:
            if stop_event and stop_event.is_set():
                break

            result = _safe_search(session, kw, asset_type)
            if result:
                total = result.get("total", 0)
                if total > 0:
                    # Build full result dict
                    comp_level, comp_icon, comp_color = _get_competition_level(total)
                    opp_level, opp_icon, opp_color = _get_opportunity_level(total)
                    final[asset_type].append({
                        "keyword": kw,
                        "total_results": total,
                        "competition_level": comp_level,
                        "competition_icon": comp_icon,
                        "competition_color": comp_color,
                        "opportunity_level": opp_level,
                        "opportunity_icon": opp_icon,
                        "opportunity_color": opp_color,
                        "asset_type": asset_type,
                    })

            time.sleep(2)

        # Sort by total results descending (most popular = trending)
        final[asset_type].sort(key=lambda x: x.get("total_results", 0), reverse=True)
        # Keep top 5
        final[asset_type] = final[asset_type][:5]

        # Extra pause between categories
        if asset_type != asset_types[-1]:
            time.sleep(3)

    return final


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
        f'Generate exactly 20 related keywords/phrases that are:\n'
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


def niche_gap_finder(keyword, asset_type="all", stop_event=None,
                     on_progress=None, on_keyword_done=None):
    """
    Niche Gap Finder — find low-competition keyword opportunities.
    
    1. Search the main keyword on Adobe Stock to get longtail suggestions
    2. Also fetch autocomplete suggestions
    3. Analyze each suggestion's result count
    4. Return sorted by opportunity (lowest results = best opportunity)
    
    No API key needed — uses Adobe Stock scraping only.
    """
    if not HAS_REQUESTS:
        return []

    session = AdobeStockSession.get_instance()
    
    # Phase 1: Collect candidate keywords from multiple sources
    candidates = set()
    
    # Source 1: Longtail keywords from main search
    try:
        result = session.search(keyword, asset_type=asset_type)
        longtails = result.get("longtail_keywords", [])
        for lt in longtails:
            kw_text = ""
            if isinstance(lt, dict):
                kw_text = lt.get("text", lt.get("keyword", "")).strip()
            elif isinstance(lt, str):
                kw_text = lt.strip()
            if kw_text and len(kw_text) >= 3 and kw_text.lower() != keyword.lower():
                candidates.add(kw_text)
    except Exception as e:
        logger.warning(f"Longtail fetch failed for '{keyword}': {e}")
    
    time.sleep(1)
    
    # Source 2: Autocomplete suggestions
    try:
        autocomplete = _fetch_autocomplete(session, keyword)
        for kw in autocomplete:
            if kw and len(kw) >= 3 and kw.lower() != keyword.lower():
                candidates.add(kw)
    except Exception as e:
        logger.warning(f"Autocomplete fetch failed for '{keyword}': {e}")
    
    time.sleep(0.5)
    
    # Source 3: Try variations with common modifiers
    modifiers = ["background", "abstract", "pattern", "texture", "design",
                 "illustration", "art", "concept", "template", "modern"]
    for mod in modifiers:
        if stop_event and stop_event.is_set():
            break
        variation = f"{keyword} {mod}"
        if variation.lower() not in {c.lower() for c in candidates}:
            candidates.add(variation)
    
    # Limit candidates to avoid too many requests
    candidate_list = list(candidates)[:25]
    
    if not candidate_list:
        return []
    
    # Phase 2: Look up result count for each candidate
    results = []
    total = len(candidate_list)
    
    for i, kw in enumerate(candidate_list):
        if stop_event and stop_event.is_set():
            break
        
        result = search_adobe_stock(kw, asset_type=asset_type)
        results.append(result)
        
        if on_keyword_done:
            on_keyword_done(result)
        if on_progress:
            on_progress(i + 1, total)
        
        # Rate limit
        time.sleep(0.8)
    
    # Sort by total results ascending (lowest = best opportunity)
    results.sort(key=lambda x: x.get("total_results", 0) if x.get("total_results", -1) >= 0 else float("inf"))
    
    return results


def _fetch_autocomplete(session, keyword):
    """
    Fetch autocomplete suggestions from Adobe Stock search.
    Returns a list of keyword strings.
    """
    encoded_kw = urllib.parse.quote_plus(keyword.strip())
    
    # Adobe Stock autocomplete endpoint
    url = "https://adobestock.com/Ajax/Search"
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"https://stock.adobe.com/search?k={encoded_kw}",
        "X-Requested-With": "XMLHttpRequest",
    }
    params = {
        "k": keyword.strip(),
        "limit": "1",
        "search_type": "autosuggest",
    }
    
    try:
        resp = session._session.get(
            "https://stock.adobe.com/Ajax/Search",
            params=params, headers=headers, timeout=12
        )
        if resp.status_code == 200:
            data = resp.json()
            suggestions = []
            # Try different possible response formats
            for key in ("longtail_keywords", "suggestions", "autocomplete"):
                items = data.get(key, [])
                for item in items:
                    if isinstance(item, dict):
                        text = item.get("text", item.get("keyword", "")).strip()
                    elif isinstance(item, str):
                        text = item.strip()
                    else:
                        continue
                    if text:
                        suggestions.append(text)
            return suggestions
    except Exception as e:
        logger.warning(f"Autocomplete request failed: {e}")
    
    return []


def format_number(num):
    """Format a number with commas for display."""
    if num < 0:
        return "N/A"
    return f"{num:,}"
