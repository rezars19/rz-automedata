"""
RZ Automedata - AI Provider Module
Supports Groq, Maia Router, and OpenRouter for metadata generation.
All providers use OpenAI-compatible chat completion API with vision.
"""

import requests
import json
import base64
import re
import time


# Words that should NEVER appear at the end of a title/description.
# These are articles, prepositions, conjunctions, and other "dangling" words.
_DANGLING_TAIL_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'nor', 'for', 'yet', 'so',
    'in', 'on', 'at', 'to', 'of', 'by', 'as', 'is', 'it', 'its',
    'with', 'from', 'into', 'that', 'this', 'than', 'then',
    'are', 'was', 'were', 'be', 'been', 'being',
    'has', 'have', 'had', 'do', 'does', 'did',
    'will', 'would', 'shall', 'should', 'may', 'might', 'can', 'could',
    'not', 'no', 'if', 'when', 'where', 'while', 'which', 'who',
    'their', 'our', 'your', 'his', 'her',
}


def _strip_dangling_tail(text):
    """Remove trailing articles/prepositions/conjunctions that make no sense at the end."""
    if not text:
        return text
    # Also strip trailing punctuation that isn't a period
    text = text.rstrip()
    while text:
        words = text.rsplit(None, 1)
        if len(words) < 2:
            break  # Single word left, keep it
        last_word = words[-1].rstrip('.,;:!?').lower()
        if last_word in _DANGLING_TAIL_WORDS:
            text = words[0].rstrip()
        else:
            break
    # Remove trailing comma/semicolon but keep period
    text = text.rstrip(',;: ')
    return text


def _truncate_to_complete_word(text, max_length):
    """Truncate text to max_length, ensuring it ends at a complete word boundary."""
    if not text or len(text) <= max_length:
        return _strip_dangling_tail(text)
    # Cut to max_length
    truncated = text[:max_length]
    # If the next character (beyond max_length) is a space or we're at a word boundary, we're fine
    if len(text) > max_length and text[max_length] == ' ':
        return _strip_dangling_tail(truncated.rstrip())
    # Otherwise, find the last space to avoid cutting mid-word
    last_space = truncated.rfind(' ')
    if last_space > 0:
        return _strip_dangling_tail(truncated[:last_space].rstrip())
    return _strip_dangling_tail(truncated)  # Single long word, just cut


def _try_repair_truncated_json(text):
    """Attempt to repair a truncated JSON response from AI.
    
    When max_tokens cuts off the response mid-JSON, this tries to
    close any open strings and braces to make it parseable.
    Handles common truncation patterns like:
      {"title": "...", "keywords": "kw1, kw2, kw3, incompl
    """
    text = text.strip()
    if not text:
        return text
    
    # If it already parses, return as-is
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    
    # Try to repair: close open strings and braces
    repaired = text
    
    # Count unescaped quotes to see if a string is open
    in_string = False
    i = 0
    while i < len(repaired):
        c = repaired[i]
        if c == '\\' and in_string:
            i += 2  # skip escaped character
            continue
        if c == '"':
            in_string = not in_string
        i += 1
    
    # If we're inside an open string, close it
    if in_string:
        # Find the last opening quote (the one that started this value)
        last_quote = repaired.rfind('"')
        if last_quote >= 0:
            content_after_quote = repaired[last_quote + 1:]
            # Trim to last complete word (remove truncated fragment)
            last_comma = content_after_quote.rfind(',')
            last_space = content_after_quote.rfind(' ')
            # For keywords field: trim to last complete keyword (after comma)
            if last_comma > 0 and last_comma > last_space - 10:
                # Cut at last comma to keep complete keywords
                repaired = repaired[:last_quote + 1 + last_comma]
            elif last_space > 0:
                repaired = repaired[:last_quote + 1 + last_space]
            repaired += '"'
    
    # Close any open brackets
    open_brackets = repaired.count('[') - repaired.count(']')
    repaired += ']' * max(0, open_brackets)
    
    # Close any open braces
    open_braces = repaired.count('{') - repaired.count('}')
    repaired += '}' * max(0, open_braces)
    
    # Try to parse the repaired version
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass
    
    # Second attempt: more aggressive repair
    # Strip everything after the last properly closed key-value pair
    # Look for pattern like: "key": "value", or "key": number,
    match = re.search(r'(.*"\s*:\s*(?:"[^"]*"|\d+)\s*),?\s*"?[^}]*$', text, re.DOTALL)
    if match:
        repaired = match.group(1) + '}'
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            pass
    
    # Last resort: return original
    return text


# ─── Adobe Stock Categories ─────────────────────────────────────────────────────
ADOBE_STOCK_CATEGORIES = {
    1: "Animals",
    2: "Buildings and Architecture",
    3: "Business",
    4: "Drinks",
    5: "The Environment",
    6: "States of Mind",
    7: "Food",
    8: "Graphic Resources",
    9: "Hobbies and Leisure",
    10: "Industry",
    11: "Landscapes",
    12: "Lifestyle",
    13: "People",
    14: "Plants and Flowers",
    15: "Culture and Religion",
    16: "Science",
    17: "Social Issues",
    18: "Sports",
    19: "Technology",
    20: "Transport",
    21: "Travel"
}

CATEGORY_LIST_STR = "\n".join([f"{k}: {v}" for k, v in ADOBE_STOCK_CATEGORIES.items()])


# ─── Shutterstock Categories ─────────────────────────────────────────────────
SHUTTERSTOCK_CATEGORIES = [
    "Animals/Wildlife",
    "Backgrounds/Textures",
    "Buildings/Landmarks",
    "Business/Finance",
    "Education",
    "Food and Drink",
    "Healthcare/Medical",
    "Holidays",
    "Industrial",
    "Nature",
    "Objects",
    "People",
    "Religion",
    "Science",
    "Signs/Symbols",
    "Sports/Recreation",
    "Technology",
    "Transportation"
]

SHUTTERSTOCK_CATEGORY_LIST_STR = "\n".join([f"- {c}" for c in SHUTTERSTOCK_CATEGORIES])


# ─── Freepik AI Models ────────────────────────────────────────────────────────
FREEPIK_MODELS = [
    "Adobe Firefly", "Dall-e 1", "Dall-e 2", "Dall-e 3", "Flux 1.0", "Flux 1.0 Fast",
    "Flux 1.0 Realism", "Flux 1.1", "Flux Kontext [Max]", "Flux Kontext [Pro]", "Freepik Classic",
    "Freepik Classic Fast", "Freepik Flux", "Freepik Flux Fast", "Freepik Flux Realism",
    "Freepik Mystic 1.0", "Freepik Mystic 2.5", "Freepik Mystic 2.5 Flexible", "Freepik Mystic 2.5 Fluid",
    "Freepik Pikaso", "Google Imagen 3", "Google Imagen 4", "Google Imagen 4 Fast", "Google Imagen 4 Ultra",
    "Google Nano Banana", "GPT", "GPT 1 - HQ", "Ideogram 1.0", "Ideogram 3", "Leonardo", "Midjourney 1",
    "Midjourney 2", "Midjourney 3", "Midjourney 4", "Midjourney 5", "Midjourney 5.1", "Midjourney 5.2",
    "Midjourney 6", "niji", "Runway", "Seedream", "Stable Diffusion 1.4", "Stable Diffusion 1.5",
    "Stable Diffusion 2.0", "Stable Diffusion 2.1", "Stable Diffusion XL", "Wepik"
]


# ─── Provider Configurations ────────────────────────────────────────────────────
PROVIDERS = {
    "Groq": {
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "models": [
            "meta-llama/llama-4-scout-17b-16e-instruct"
        ]
    },
    "Maia Router": {
        "base_url": "https://api.maiarouter.ai/v1/chat/completions",
        "models": [
            "maia/gemini-2.5-flash-lite",
            "maia/gemini-2.0-flash",
            "maia/gemini-1.5-flash",
            "openai/gpt-4.1-nano"
        ]
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "models": [
            "google/gemini-2.5-flash-lite",
            "openai/gpt-4.1-nano",
            "google/gemini-2.0-flash-lite-001",
            "google/gemini-2.0-flash-001"
        ]
    }
}


def get_provider_names():
    """Return list of provider names."""
    return list(PROVIDERS.keys())


def get_models_for_provider(provider_name):
    """Return list of models for a given provider."""
    return PROVIDERS.get(provider_name, {}).get("models", [])


def _build_custom_instructions(custom_prompt, rule_num="5"):
    """Build custom prompt instructions if provided."""
    if not custom_prompt:
        return ""
    custom_keywords = [kw.strip() for kw in custom_prompt.split(",") if kw.strip()]
    if not custom_keywords:
        return ""
    kw_list_str = ", ".join(custom_keywords)
    return f"""

{rule_num}. **MANDATORY Custom Keywords** (CRITICAL - YOU MUST FOLLOW THIS):
   - The following keywords MUST appear in BOTH the description/title AND the keywords list: [{kw_list_str}]
   - For the DESCRIPTION/TITLE: Naturally incorporate ALL of these words/phrases. It must still read naturally and be coherent - do NOT just append them. Weave them seamlessly.
   - For the KEYWORDS: These custom keywords MUST be placed at the VERY BEGINNING of the keywords list (before any other keywords). Then fill the remaining spots with relevant keywords to reach exactly 49 total.
   - This is NON-NEGOTIABLE. Every single custom keyword must appear in both description/title and keywords."""


def _build_brand_rules():
    """Build brand name restriction rules."""
    return """**ABSOLUTELY NO BRAND NAMES, TRADEMARKS, OR INTELLECTUAL PROPERTY** (CRITICAL - STRICTLY ENFORCED):
   - NEVER use ANY brand names, company names, or trademarks in the title/description or keywords. This includes but is not limited to:
     * Technology brands: Apple, iPhone, iPad, Samsung, Google, Microsoft, Windows, Android, Sony, Canon, Nikon, etc.
     * Fashion/Sports brands: Nike, Adidas, Gucci, Louis Vuitton, Puma, Reebok, Zara, H&M, etc.
     * Food/Drink brands: Coca-Cola, Pepsi, Starbucks, McDonald's, Nestle, etc.
     * Automotive brands: Tesla, BMW, Mercedes, Toyota, Ferrari, Lamborghini, etc.
     * Social media: Facebook, Instagram, TikTok, Twitter/X, YouTube, WhatsApp, Snapchat, etc.
     * ANY other recognizable brand or company name worldwide.
   - NEVER use copyrighted character names (Disney characters, Marvel/DC superheroes, anime characters, etc.).
   - NEVER use movie titles, TV show names, song titles, book titles, or game names.
   - NEVER mention logos, brand symbols, or trademarked slogans.
   - NEVER reference specific product model names (e.g., "Galaxy S24", "MacBook Pro", "Air Jordan").
   - INSTEAD, use GENERIC descriptive terms:
     * "smartphone" instead of "iPhone" or "Samsung Galaxy"
     * "athletic shoes" or "running shoes" instead of "Nike sneakers"
     * "luxury car" or "sports car" instead of "Ferrari" or "BMW"
     * "social media app" instead of "Instagram" or "TikTok"
     * "coffee shop" instead of "Starbucks"
     * "streaming service" instead of "Netflix"
     * "search engine" instead of "Google"
     * "laptop computer" instead of "MacBook"
     * "energy drink" instead of "Red Bull"
     * "fast food restaurant" instead of "McDonald's"
   - This rule applies even if the brand is clearly visible in the image. Describe the OBJECT, not the BRAND.
   - Violating this rule will cause the asset to be REJECTED. This is NON-NEGOTIABLE."""


def _build_category_rules():
    """Build category selection rules shared by both platforms."""
    return """**CATEGORY SELECTION RULES (CRITICAL - FOLLOW STRICTLY):**
   - Choose the category based on the PRIMARY/MAIN subject of the image, NOT secondary elements.
   - **Animals**: If the image features ANY animal as the main subject, ALWAYS use the Animals category. This includes:
     * Realistic animals, cartoon animals, illustrated animals, 3D rendered animals
     * Anthropomorphic animals (animals with human characteristics, wearing clothes, standing upright, talking)
     * Animal mascots, animal characters, cute animal illustrations
     * Even if the animal looks like a person or acts like a person, if it IS an animal → Animals
   - **People**: ONLY use this when the main subject is actual HUMAN beings (not animals dressed as humans)
   - **Plants/Flowers/Nature**: When flowers/plants are the PRIMARY subject (not just background decoration)
   - **Food**: When food items are the main subject
   - **Backgrounds/Textures/Graphic Resources**: For abstract patterns, textures, backgrounds, templates, icons, UI elements
   - Always ask yourself: "What is the MAIN THING in this image?" and categorize based on that answer"""


def _build_prompt(filename, file_type, custom_prompt=""):
    """Build the system + user prompt for Adobe Stock metadata generation."""

    custom_instructions = _build_custom_instructions(custom_prompt)
    brand_rules = _build_brand_rules()
    category_rules = _build_category_rules()

    system_prompt = f"""You are an expert Adobe Stock metadata specialist and SEO expert. Your task is to analyze the provided image(s) and generate highly SEO-optimized metadata that will make the asset appear at the TOP of Adobe Stock search results.

STRICT RULES:

1. **Title** (CRITICAL - MUST be between 180 and 199 characters):
   - The title MUST be at minimum 180 characters and at maximum 199 characters. Count carefully.
   - Write a rich, descriptive, SEO-optimized title in English.
   - Naturally incorporate high-value search terms that buyers would use to find this asset.
   - Describe the subject, scene, mood, style, colors, and context in flowing natural language.
   - Do NOT use keyword stuffing. Make it read naturally like a professional description.
   - Do NOT use special characters, quotes, or line breaks.
   - The title MUST end with a COMPLETE word. NEVER cut off a word in the middle. Every word must be fully spelled out.
   - DOUBLE-CHECK: Read your title back. Does the last word make sense on its own? If not, fix it.
   - Example good title (185 chars): "Young professional businesswoman working on laptop computer in modern bright office workspace with natural light streaming through large windows creating productive atmosphere"

2. **Keywords** (exactly 49 keywords, comma-separated):
   - Generate EXACTLY 49 keywords or short phrases (1-3 words each).
   - RANK BY IMPORTANCE: most relevant and high-search-volume keywords FIRST.
   - Include: main subject, actions, emotions, style, colors, composition, mood, concepts, use-cases, related themes.
   - Use terms that Adobe Stock buyers actually search for.
   - Separate each keyword with a comma.
   - Write in English.
   - **ABSOLUTELY NO DUPLICATE KEYWORDS.** Every keyword must be unique. Do NOT repeat any word or phrase, even in different forms (e.g., do not use both "dog" and "dogs", or "beautiful" and "beautiful design"). Each keyword must add NEW meaning.

3. **Category** (choose ONE number from the list below that BEST matches the PRIMARY subject of the image):
{CATEGORY_LIST_STR}

   {category_rules}

4. {brand_rules}
{custom_instructions}

RESPOND WITH ONLY valid JSON in this exact format, no other text:
{{"title": "Your 180-199 character SEO optimized descriptive title here", "keywords": "keyword1, keyword2, keyword three, keyword4, ...(49 total)", "category": 13}}"""

    custom_reminder = ""
    if custom_prompt:
        custom_reminder = f" IMPORTANT: You MUST include these custom keywords in both title and at the beginning of keywords: [{custom_prompt}]."

    brand_reminder = " CRITICAL REMINDER: Do NOT use any brand names, trademarks, copyrighted names, or intellectual property in the title or keywords. Use only generic descriptive terms."

    if file_type == "video":
        user_text = f"Analyze these video frames from the file '{filename}'. The frames are sequential snapshots from the video. Describe the overall video content and generate metadata based on ALL frames combined. Remember: title MUST be 180-199 characters, exactly 49 keywords ranked by importance, and pick the most accurate category.{custom_reminder}{brand_reminder}"
    elif file_type == "vector":
        user_text = f"Analyze this vector graphic file '{filename}'. The image provided is either a rasterized version of the vector or a descriptive summary of its contents. Use the filename as a strong hint about the subject matter. Generate Adobe Stock metadata for this vector illustration. Remember: title MUST be 180-199 characters, exactly 49 keywords ranked by importance, and pick the most accurate category.{custom_reminder}{brand_reminder}"
    else:
        user_text = f"Analyze this image file '{filename}' and generate Adobe Stock metadata. Remember: title MUST be 180-199 characters, exactly 49 keywords ranked by importance, and pick the most accurate category.{custom_reminder}{brand_reminder}"

    return system_prompt, user_text


def _build_shutterstock_prompt(filename, file_type, custom_prompt=""):
    """Build the system + user prompt for Shutterstock metadata generation."""

    custom_instructions = _build_custom_instructions(custom_prompt)
    brand_rules = _build_brand_rules()
    category_rules = _build_category_rules()

    system_prompt = f"""You are an expert Shutterstock metadata specialist and SEO expert. Your task is to analyze the provided image(s) and generate highly SEO-optimized metadata that will make the asset appear at the TOP of Shutterstock search results.

STRICT RULES:

1. **Description** (CRITICAL - MUST be a rich, SEO-optimized description, maximum 2000 characters):
   - Write a detailed, descriptive, SEO-optimized description in English.
   - Maximum 2000 characters. Aim for at least 150 characters for good SEO.
   - Naturally incorporate high-value search terms that buyers would use to find this asset.
   - Describe the subject, scene, mood, style, colors, and context in flowing natural language.
   - Do NOT use keyword stuffing. Make it read naturally like a professional description.
   - Do NOT use line breaks. Use a single paragraph.
   - The description MUST end with a COMPLETE word and sentence. NEVER cut off mid-word or mid-sentence.

2. **Keywords** (exactly 49 keywords, comma-separated):
   - Generate EXACTLY 49 keywords or short phrases (1-3 words each).
   - RANK BY IMPORTANCE: most relevant and high-search-volume keywords FIRST.
   - Include: main subject, actions, emotions, style, colors, composition, mood, concepts, use-cases, related themes.
   - Use terms that Shutterstock buyers actually search for.
   - Separate each keyword with a comma.
   - Write in English.
   - **ABSOLUTELY NO DUPLICATE KEYWORDS.** Every keyword must be unique. Do NOT repeat any word or phrase, even in different forms (e.g., do not use both "dog" and "dogs", or "beautiful" and "beautiful design"). Each keyword must add NEW meaning.

3. **Categories** (choose TWO categories from the list below):
   - category1: The PRIMARY/MAIN category that BEST matches the image content
   - category2: A SECONDARY category that also relates to the image content
   Available categories:
{SHUTTERSTOCK_CATEGORY_LIST_STR}

   {category_rules}

4. {brand_rules}
{custom_instructions}

RESPOND WITH ONLY valid JSON in this exact format, no other text:
{{"description": "Your rich SEO-optimized description here (max 2000 chars)", "keywords": "keyword1, keyword2, keyword three, keyword4, ...(49 total)", "category1": "Animals/Wildlife", "category2": "Nature"}}"""

    custom_reminder = ""
    if custom_prompt:
        custom_reminder = f" IMPORTANT: You MUST include these custom keywords in both description and at the beginning of keywords: [{custom_prompt}]."

    brand_reminder = " CRITICAL REMINDER: Do NOT use any brand names, trademarks, copyrighted names, or intellectual property in the description or keywords. Use only generic descriptive terms."

    if file_type == "video":
        user_text = f"Analyze these video frames from the file '{filename}'. The frames are sequential snapshots from the video. Describe the overall video content and generate metadata based on ALL frames combined. Remember: description max 2000 characters, exactly 49 keywords ranked by importance, and pick the 2 most accurate categories.{custom_reminder}{brand_reminder}"
    elif file_type == "vector":
        user_text = f"Analyze this vector graphic file '{filename}'. The image provided is either a rasterized version of the vector or a descriptive summary of its contents. Use the filename as a strong hint about the subject matter. Generate Shutterstock metadata for this vector illustration. Remember: description max 2000 characters, exactly 49 keywords ranked by importance, and pick the 2 most accurate categories.{custom_reminder}{brand_reminder}"
    else:
        user_text = f"Analyze this image file '{filename}' and generate Shutterstock metadata. Remember: description max 2000 characters, exactly 49 keywords ranked by importance, and pick the 2 most accurate categories.{custom_reminder}{brand_reminder}"

    return system_prompt, user_text


def _build_freepik_prompt(filename, file_type, custom_prompt="", ai_generated=False):
    """Build the system + user prompt for Freepik metadata generation."""

    custom_instructions = _build_custom_instructions(custom_prompt)
    brand_rules = _build_brand_rules()

    prompt_section = ""
    prompt_json_field = ""
    if ai_generated:
        prompt_section = """3. **Prompt** (the AI generation prompt that would recreate this image):
   - Write a detailed, descriptive prompt that an AI image generator would use to create this exact image.
   - Include style, composition, colors, mood, lighting, and specific details.
   - Write in English, natural language.
   - Be specific enough that an AI model could recreate a very similar image.
   - NEVER start the prompt with "Create", "Generate", "Design", "Make", "Produce", "Draw", or "Render".
   - Start DIRECTLY with the subject description. Example: "A sleek modern brown glass bottle on a beige surface" NOT "Create an image of a brown glass bottle".

4. """
        prompt_json_field = ', "prompt": "A sleek modern brown glass bottle on a beige surface with soft lighting"'
    else:
        prompt_section = "3. "
        prompt_json_field = ""

    system_prompt = f"""You are an expert Freepik metadata specialist and SEO expert. Your task is to analyze the provided image(s) and generate highly SEO-optimized metadata for Freepik.

STRICT RULES:

1. **Title** (CRITICAL - must be SEO-optimized, maximum 100 words):
   - Write a rich, descriptive, SEO-optimized title in English.
   - Naturally incorporate high-value search terms that buyers would use.
   - Describe the subject, scene, mood, style, colors, and context.
   - Do NOT use special characters, quotes, or line breaks.
   - The title MUST end with a COMPLETE word. NEVER cut off a word in the middle. Every word must be fully spelled out.
   - DOUBLE-CHECK: Read your title back. Does the last word make sense? If not, fix it.

2. **Keywords** (exactly 49 keywords, comma-separated):
   - Generate EXACTLY 49 keywords or short phrases (1-3 words each).
   - RANK BY IMPORTANCE: most relevant and high-search-volume keywords FIRST.
   - Include: main subject, actions, emotions, style, colors, composition, mood, concepts.
   - Separate each keyword with a comma.
   - **ABSOLUTELY NO DUPLICATE KEYWORDS.** Every keyword must be unique. Do NOT repeat any word or phrase, even in different forms (e.g., do not use both "dog" and "dogs", or "beautiful" and "beautiful design"). Each keyword must add NEW meaning.

{prompt_section}{brand_rules}
{custom_instructions}

RESPOND WITH ONLY valid JSON in this exact format, no other text:
{{"title": "Your SEO optimized title here", "keywords": "keyword1, keyword2, keyword three, ...(49 total)"{prompt_json_field}}}"""

    custom_reminder = ""
    if custom_prompt:
        custom_reminder = f" IMPORTANT: You MUST include these custom keywords in both title and at the beginning of keywords: [{custom_prompt}]."

    brand_reminder = " CRITICAL REMINDER: Do NOT use any brand names, trademarks, copyrighted names, or intellectual property in the title or keywords. Use only generic descriptive terms."

    if file_type == "video":
        user_text = f"Analyze these video frames from the file '{filename}'. Generate Freepik metadata. Remember: title max 100 words, exactly 49 keywords ranked by importance.{custom_reminder}{brand_reminder}"
    elif file_type == "vector":
        user_text = f"Analyze this vector graphic file '{filename}'. Generate Freepik metadata for this vector. Remember: title max 100 words, exactly 49 keywords ranked by importance.{custom_reminder}{brand_reminder}"
    else:
        user_text = f"Analyze this image file '{filename}' and generate Freepik metadata. Remember: title max 100 words, exactly 49 keywords ranked by importance.{custom_reminder}{brand_reminder}"

    return system_prompt, user_text


def _encode_image_to_base64(image_pil):
    """Convert a PIL Image to base64 string."""
    import io
    buffer = io.BytesIO()
    image_pil.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


def _build_messages(images_base64, filename, file_type, custom_prompt="", platform="adobestock", ai_generated=False):
    """Build the messages array for the API call."""
    if platform == "freepik":
        system_prompt, user_text = _build_freepik_prompt(filename, file_type, custom_prompt, ai_generated=ai_generated)
    elif platform == "shutterstock":
        system_prompt, user_text = _build_shutterstock_prompt(filename, file_type, custom_prompt)
    else:
        system_prompt, user_text = _build_prompt(filename, file_type, custom_prompt)

    content = [{"type": "text", "text": user_text}]
    for img_b64 in images_base64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{img_b64}"
            }
        })

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content}
    ]
    return messages


def _parse_response(response_text, custom_prompt="", platform="adobestock"):
    """Parse JSON from the AI response, handling markdown code blocks."""
    text = response_text.strip()

    # Try to extract JSON from markdown code block
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()

    # Try to find JSON object - use greedy match to get the full object
    # First try to find a complete JSON object with balanced braces
    json_obj_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_obj_match:
        text = json_obj_match.group(0)
    else:
        # If no closing brace found, the response might be truncated
        # Try to find at least an opening brace
        json_start = text.find('{')
        if json_start >= 0:
            text = text[json_start:]

    # Attempt to repair truncated JSON before parsing
    text = _try_repair_truncated_json(text)

    try:
        data = json.loads(text)

        # ── Parse keywords (shared by all platforms) ────────────────────
        keywords_raw = str(data.get("keywords", "")).strip()
        # Split carefully: keywords may contain commas inside phrases
        kw_list = [kw.strip() for kw in keywords_raw.split(",") if kw.strip()]

        # Enforce custom keywords at the beginning of keywords list
        if custom_prompt:
            custom_keywords = [kw.strip() for kw in custom_prompt.split(",") if kw.strip()]
            custom_lower = [ck.lower() for ck in custom_keywords]
            kw_list = [kw for kw in kw_list if kw.lower() not in custom_lower]
            kw_list = custom_keywords + kw_list

        # Remove empty / whitespace-only entries that may inflate the count
        kw_list = [kw for kw in kw_list if kw.strip()]

        # ── Remove duplicate keywords (case-insensitive) ────────────────
        seen = set()
        unique_kw_list = []
        for kw in kw_list:
            kw_lower = kw.strip().lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique_kw_list.append(kw)
        kw_list = unique_kw_list

        # Ensure exactly 49 keywords (trim excess)
        if len(kw_list) > 49:
            kw_list = kw_list[:49]

        # Reconstruct keywords as a clean comma-separated string
        keywords = ", ".join(kw_list)

        if platform == "freepik":
            title = str(data.get("title", "")).strip()
            # Validate title ends on a meaningful word
            title = _strip_dangling_tail(title)
            prompt = str(data.get("prompt", "")).strip()

            # Strip "Create/Generate/Design..." prefixes from prompt
            prompt = re.sub(
                r'^(Create|Generate|Design|Make|Produce|Draw|Render)\s+(an?\s+)?(image|picture|photo|illustration|graphic|artwork|scene|visual|vector|design)?\s*(of|showing|depicting|featuring|with)?\s*',
                '', prompt, count=1, flags=re.IGNORECASE
            ).strip()
            # Capitalize the first letter after stripping
            if prompt:
                prompt = prompt[0].upper() + prompt[1:]

            return {
                "title": title,
                "keywords": keywords,
                "category": "",
                "prompt": prompt
            }
        elif platform == "shutterstock":
            description = str(data.get("description", "")).strip()
            if len(description) > 2000:
                description = _truncate_to_complete_word(description, 2000)
            # Validate description ends on a meaningful word
            description = _strip_dangling_tail(description)

            category1 = str(data.get("category1", "")).strip()
            category2 = str(data.get("category2", "")).strip()

            # Validate categories against Shutterstock list
            if category1 not in SHUTTERSTOCK_CATEGORIES:
                category1 = SHUTTERSTOCK_CATEGORIES[0]
            if category2 not in SHUTTERSTOCK_CATEGORIES:
                category2 = SHUTTERSTOCK_CATEGORIES[1] if category1 != SHUTTERSTOCK_CATEGORIES[1] else SHUTTERSTOCK_CATEGORIES[0]

            return {
                "title": description,
                "keywords": keywords,
                "category": f"{category1},{category2}"
            }
        else:
            title = str(data.get("title", "")).strip()
            category = str(data.get("category", "")).strip()

            if len(title) > 199:
                title = _truncate_to_complete_word(title, 199)
            # Validate title ends on a meaningful word
            title = _strip_dangling_tail(title)

            # Validate category is a number 1-21
            try:
                cat_num = int(category)
                if cat_num < 1 or cat_num > 21:
                    category = "1"
                else:
                    category = str(cat_num)
            except ValueError:
                category = "1"

            return {
                "title": title,
                "keywords": keywords,
                "category": category
            }
    except json.JSONDecodeError:
        raise ValueError(f"Failed to parse AI response as JSON: {response_text[:300]}")


def generate_metadata(provider_name, model, api_key, images_pil, filename, file_type="image", custom_prompt="", platform="adobestock", ai_generated=False):
    """
    Generate metadata using the specified AI provider.
    
    Args:
        provider_name: "Groq", "Maia Router", or "OpenRouter"
        model: Model identifier string
        api_key: API key for the provider
        images_pil: List of PIL Image objects to analyze
        filename: Original filename of the asset
        file_type: "image", "vector", or "video"
        custom_prompt: Custom keywords to include in title and keywords
        platform: "adobestock", "shutterstock", or "freepik"
        ai_generated: For Freepik, whether AI Generated checkbox is on
    
    Returns:
        dict with keys: title, keywords, category (and prompt for freepik)
    
    Raises:
        Exception on API or parsing errors
    """
    provider = PROVIDERS.get(provider_name)
    if not provider:
        raise ValueError(f"Unknown provider: {provider_name}")

    # Convert PIL images to base64
    images_base64 = [_encode_image_to_base64(img) for img in images_pil]

    # Build request
    messages = _build_messages(images_base64, filename, file_type, custom_prompt, platform=platform, ai_generated=ai_generated)
    url = provider["base_url"]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    # OpenRouter requires these headers for authentication
    if provider_name == "OpenRouter":
        headers["HTTP-Referer"] = "https://rz-automedata.app"
        headers["X-Title"] = "RZ Automedata"

    # 4096 tokens is needed for: title (180-199 chars) + 49 keywords + category
    # 2048 was causing truncation, especially for video files with long filenames
    max_tok = 4096

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tok,
        "temperature": 0.3
    }

    # Debug: log key info for troubleshooting
    masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
    print(f"[DEBUG] Provider: {provider_name}, URL: {url}")
    print(f"[DEBUG] API Key: {masked_key} (len={len(api_key)})")
    print(f"[DEBUG] Headers: {list(headers.keys())}")

    # ── API call with retry for transient server errors ────────────────
    MAX_RETRIES = 3
    RETRY_STATUSES = {500, 502, 503, 429}   # server errors + rate limit
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.exceptions.Timeout:
            last_error = "Request timed out (60s). The server may be overloaded."
            if attempt < MAX_RETRIES:
                wait = 3 * (2 ** (attempt - 1))  # 3s, 6s, 12s
                print(f"[RETRY] Attempt {attempt}/{MAX_RETRIES} timed out, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise Exception(last_error)
        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {e}"
            if attempt < MAX_RETRIES:
                wait = 3 * (2 ** (attempt - 1))
                print(f"[RETRY] Attempt {attempt}/{MAX_RETRIES} connection error, waiting {wait}s...")
                time.sleep(wait)
                continue
            raise Exception(last_error)

        if response.status_code == 200:
            break  # Success — exit retry loop

        error_text = response.text[:500]
        print(f"[DEBUG] Response status: {response.status_code}")
        print(f"[DEBUG] Response body: {error_text}")

        if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
            wait = 3 * (2 ** (attempt - 1))  # 3s, 6s, 12s
            print(f"[RETRY] Attempt {attempt}/{MAX_RETRIES} got {response.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue

        # Non-retryable error or final attempt
        raise Exception(f"API Error ({response.status_code}): {error_text}")

    resp_json = response.json()

    # Extract content from response
    try:
        content = resp_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print(f"[DEBUG] Full response: {json.dumps(resp_json)[:800]}")
        raise Exception(f"Unexpected API response structure: {json.dumps(resp_json)[:500]}")

    if not content or not content.strip():
        print(f"[DEBUG] Empty response from model. Full response: {json.dumps(resp_json)[:800]}")
        raise Exception(
            f"Model '{model}' returned an empty response. "
            f"This model may not support vision/image inputs. "
            f"Try a different model (e.g. gemini-2.5-flash-lite or gpt-4.1-nano)."
        )

    return _parse_response(content, custom_prompt, platform=platform)
