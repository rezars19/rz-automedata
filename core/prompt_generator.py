"""
RZ Automedata - AI Prompt Generator for Microstock
Generates high-quality, unique prompts for images, vectors, and videos
using the user's configured AI provider.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# ─── Vector Style Options ─────────────────────────────────────────────────────

VECTOR_STYLES = [
    "Flat 2D",
    "3D Render",
    "Isometric",
    "Line Art",
    "Cartoon",
    "Minimal",
    "Geometric",
    "Hand Drawn",
    "Watercolor",
    "Gradient",
]

# ─── System Prompts ───────────────────────────────────────────────────────────

_IMAGE_SYSTEM = (
    "You are the world's best microstock prompt engineer specializing in Adobe Stock. "
    "Your prompts produce images that are the best, commercially valuable, and NEVER similar "
    "to common stock photos. Each prompt must be ultra-detailed, specifying:\n"
    "- Subject with precise visual details (age, expression, clothing, posture)\n"
    "- Environment/setting with specific elements\n"
    "- Lighting (type, direction, color temperature, mood)\n"
    "- Camera angle and lens (focal length, depth of field)\n"
    "- Color palette and mood/atmosphere\n"
    "- Resolution mandate: ultra-sharp, hyper-detailed, 8K UHD resolution\n"
    "- Photographic style (editorial, lifestyle, conceptual, etc.)\n\n"
    "CRITICAL RULES:\n"
    "- Every prompt MUST include 'ultra-sharp detail, 8K UHD, hyper-realistic'\n"
    "- Make each prompt visually DISTINCT from the others\n"
    "- Avoid generic stock photo clichés (handshake, thumbs up, etc.)\n"
    "- Focus on commercially trending, buyer-demanded concepts\n"
    "- Each prompt should be 3-5 sentences long, richly descriptive"
)

_VECTOR_SYSTEM = (
    "You are the world's best microstock vector prompt engineer specializing in Adobe Stock. "
    "Your prompts produce vector illustrations that are UNIQUE, trendy, and commercially "
    "valuable. Each prompt MUST:\n"
    "- Describe the subject in rich visual detail\n"
    "- Specify the exact style: {style}\n"
    "- Include specific color palette suggestions\n"
    "- ALWAYS end with: 'White background, no text, no watermark, no logo, "
    "no signature, clean isolated vector illustration'\n\n"
    "CRITICAL RULES:\n"
    "- Every prompt MUST match the '{style}' style consistently\n"
    "- Make each prompt visually DISTINCT from the others\n"
    "- Focus on commercially trending, buyer-demanded concepts\n"
    "- Keep designs clean, professional, and print-ready\n"
    "- Each prompt should be 2-4 sentences long, richly descriptive"
)

_VIDEO_SYSTEM = (
    "You are the world's best microstock video prompt engineer specializing in Adobe Stock. "
    "Your prompts produce video clips that are cimeatic, the best,and commercially valuable. "
    "Each prompt MUST be structured with these exact fields:\n\n"
    "subject: [Detailed description of the main subject]\n"
    "movement: [Specific motion/action of the subject]\n"
    "environment: [Detailed setting/location description]\n"
    "lighting: [Specific lighting setup and mood]\n"
    "camera_angle: [Exact camera position and framing]\n"
    "camera_movement: [How the camera moves during the shot]\n"
    "style: [Visual style matching the prompt's mood]\n"
    "CRITICAL RULES:\n"
    "- Make each prompt visually DISTINCT from the others\n"
    "- Focus on commercially trending, buyer-demanded footage\n"
    "- Avoid generic stock video clichés\n"
    "- Each field should be richly descriptive"
)


# ─── Prompt Generation ────────────────────────────────────────────────────────

def generate_prompts(keyword, prompt_type, count, provider_name, model, api_key,
                     vector_style=None, on_progress=None, stop_event=None):
    """
    Generate prompts using AI.

    Args:
        keyword: Topic/keyword (e.g. "horses running")
        prompt_type: "image", "vector", or "video"
        count: Number of prompts to generate (1-100)
        provider_name: AI provider name
        model: Model identifier
        api_key: API key
        vector_style: Style for vector prompts (from VECTOR_STYLES)
        on_progress: Callback(status_text)
        stop_event: Threading event to cancel

    Returns:
        list[str]: Generated prompts
    """
    if not provider_name or not model or not api_key:
        logger.warning("No AI provider configured for prompt generation")
        return []

    # For large counts, generate in batches to avoid token limits
    BATCH_SIZE = 20
    if count > BATCH_SIZE:
        all_prompts = []
        total_batches = (count + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_idx in range(total_batches):
            if stop_event and stop_event.is_set():
                break
            batch_count = min(BATCH_SIZE, count - len(all_prompts))
            if batch_count <= 0:
                break

            if on_progress:
                on_progress(
                    f"⏳ Generating batch {batch_idx + 1}/{total_batches} "
                    f"({len(all_prompts)}/{count} prompts)..."
                )

            batch = _generate_single_batch(
                keyword, prompt_type, batch_count, provider_name, model, api_key,
                vector_style=vector_style, on_progress=None, stop_event=stop_event,
                batch_info=(batch_idx + 1, total_batches),
            )
            all_prompts.extend(batch)

        if on_progress:
            on_progress(f"✅ Generated {len(all_prompts)} prompts")
        return all_prompts[:count]

    # Small count: single batch
    return _generate_single_batch(
        keyword, prompt_type, count, provider_name, model, api_key,
        vector_style=vector_style, on_progress=on_progress, stop_event=stop_event,
    )


def _generate_single_batch(keyword, prompt_type, count, provider_name, model, api_key,
                            vector_style=None, on_progress=None, stop_event=None,
                            batch_info=None):
    """Generate a single batch of prompts (max ~20)."""
    # Lazy import
    try:
        from core.ai_providers import PROVIDERS
    except ImportError:
        logger.error("Cannot import AI providers")
        return []

    import requests

    provider = PROVIDERS.get(provider_name)
    if not provider:
        logger.error(f"Unknown provider: {provider_name}")
        return []

    # Select system prompt
    if prompt_type == "image":
        system_prompt = _IMAGE_SYSTEM
    elif prompt_type == "vector":
        style = vector_style or "Flat 2D"
        system_prompt = _VECTOR_SYSTEM.replace("{style}", style)
    elif prompt_type == "video":
        system_prompt = _VIDEO_SYSTEM
    else:
        logger.error(f"Unknown prompt type: {prompt_type}")
        return []

    # Build user prompt
    type_label = {"image": "photo/image", "vector": "vector illustration", "video": "video clip"}
    label = type_label.get(prompt_type, prompt_type)

    uniqueness_hint = ""
    if batch_info:
        uniqueness_hint = (
            f"\nThis is batch {batch_info[0]} of {batch_info[1]}. "
            f"Make these prompts COMPLETELY DIFFERENT from typical/common prompts. "
            f"Be creative and explore unusual angles, perspectives, and scenarios."
        )

    user_prompt = (
        f'Generate exactly {count} unique, high-quality {label} prompts '
        f'based on the keyword/topic: "{keyword}".\n\n'
        f'Return ONLY a JSON array of {count} strings. Each string is one complete prompt.\n'
        f'Example format: ["prompt 1 text here", "prompt 2 text here"]\n\n'
        f'Do NOT include numbering, labels, or any text outside the JSON array.\n'
        f'Each prompt must be COMPLETELY DIFFERENT from the others while staying '
        f'relevant to "{keyword}".{uniqueness_hint}'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    # OpenRouter requires these headers
    if provider_name == "OpenRouter":
        headers["HTTP-Referer"] = "https://rz-automedata.app"
        headers["X-Title"] = "RZ Automedata"

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": min(8192, 400 * count),  # Scale with count, cap at 8192
        "temperature": 0.9  # Higher creativity for prompts
    }

    if on_progress:
        on_progress(f"Generating {count} {label} prompts...")

    if stop_event and stop_event.is_set():
        return []

    try:
        url = provider["base_url"]
        response = requests.post(url, headers=headers, json=payload, timeout=120)

        if response.status_code != 200:
            logger.error(f"AI API error ({response.status_code}): {response.text[:300]}")
            raise Exception(f"API Error ({response.status_code}): {response.text[:300]}")

        resp_json = response.json()
        content = resp_json["choices"][0]["message"]["content"].strip()

        if not content:
            raise Exception("AI returned empty response")

        # Parse JSON array from response
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            content = json_match.group(0)

        prompts = json.loads(content)

        if not isinstance(prompts, list):
            raise Exception("AI response is not a list")

        # Clean prompts
        prompts = [p.strip() for p in prompts if isinstance(p, str) and p.strip()]

        if on_progress:
            on_progress(f"✅ Generated {len(prompts)} prompts")

        return prompts[:count]  # Limit to requested count

    except json.JSONDecodeError:
        # Try to extract prompts from non-JSON response
        logger.warning("Failed to parse JSON, trying line-by-line extraction")
        lines = content.strip().split("\n")
        prompts = []
        for line in lines:
            line = line.strip()
            # Remove numbering like "1.", "1)", "- ", etc.
            line = re.sub(r'^[\d]+[\.\\)]\s*', '', line)
            line = re.sub(r'^[-•]\s*', '', line)
            line = line.strip('"').strip("'").strip()
            if line and len(line) > 20:  # Minimum reasonable prompt length
                prompts.append(line)
        
        if prompts:
            if on_progress:
                on_progress(f"✅ Generated {len(prompts)} prompts")
            return prompts[:count]
        
        raise Exception("Failed to parse AI response into prompts")

    except Exception as e:
        logger.error(f"Prompt generation failed: {e}")
        raise

