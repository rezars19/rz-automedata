"""
RZ Automedata - Metadata Processor
Orchestrates loading assets, calling AI, and saving results.
"""

import os
import glob
from PIL import Image
from video_utils import extract_frames
from ai_providers import generate_metadata
import database as db

# ── Auto-detect Ghostscript for EPS support ──────────────────────────────────
def _setup_ghostscript():
    """Find and register Ghostscript with Pillow on Windows."""
    try:
        from PIL import EpsImagePlugin
        # Check if already available
        if EpsImagePlugin.gs_windows_binary:
            return

        # Common Ghostscript install locations on Windows
        gs_paths = glob.glob(r"C:\Program Files\gs\gs*\bin\gswin64c.exe")
        gs_paths += glob.glob(r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe")
        gs_paths += glob.glob(os.path.expanduser(r"~\gs\gs*\bin\gswin64c.exe"))

        if gs_paths:
            gs_binary = gs_paths[0]
            gs_dir = os.path.dirname(gs_binary)
            # Add to PATH
            if gs_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = gs_dir + os.pathsep + os.environ.get("PATH", "")
            # Register with Pillow
            EpsImagePlugin.gs_windows_binary = gs_binary
    except Exception:
        pass

_setup_ghostscript()

# Supported file extensions
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
VECTOR_EXTENSIONS = {'.eps', '.svg'}
VIDEO_EXTENSIONS = {'.mp4', '.mov'}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VECTOR_EXTENSIONS | VIDEO_EXTENSIONS


def get_file_type(file_path):
    """Determine file type based on extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    elif ext in VECTOR_EXTENSIONS:
        return "vector"
    elif ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def load_preview_image(file_path, file_type, size=(200, 150)):
    """
    Load a preview image for display.
    
    Returns:
        PIL.Image object
    """
    if file_type == "image":
        img = Image.open(file_path)
        img.thumbnail(size, Image.LANCZOS)
        return img
    elif file_type == "vector":
        ext = os.path.splitext(file_path)[1].lower()
        # Try multiple methods for SVG
        if ext == '.svg':
            img = _try_render_svg(file_path, size)
            if img:
                return img
            return _create_vector_placeholder(file_path, size, "SVG")
        elif ext == '.eps':
            # Try embedded TIFF/JPEG preview first (no Ghostscript needed)
            embedded = _extract_eps_preview(file_path, size)
            if embedded:
                return embedded
            img = _try_render_eps(file_path, size)
            if img:
                return img
            return _create_vector_placeholder(file_path, size, "EPS")
    elif file_type == "video":
        from video_utils import get_video_thumbnail
        return get_video_thumbnail(file_path, size)
    
    return _create_placeholder(file_path, size, "FILE")


def _try_render_svg(file_path, size):
    """Try to render SVG using available libraries or by extracting embedded images."""
    # Method 1: Wand (ImageMagick) — most reliable on Windows
    try:
        from wand.image import Image as WandImage
        import io
        with WandImage(filename=file_path, resolution=150) as wand_img:
            wand_img.format = 'png'
            wand_img.transform(resize=f'{size[0]}x{size[1]}>')
            png_blob = wand_img.make_blob('png')
        img = Image.open(io.BytesIO(png_blob))
        img.load()
        img.thumbnail(size, Image.LANCZOS)
        if img.mode != "RGB":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                bg.paste(img, mask=img.split()[3])
            else:
                bg.paste(img)
            img = bg
        return img
    except Exception:
        pass

    # Method 2: cairosvg
    try:
        import cairosvg
        import io
        png_data = cairosvg.svg2png(url=file_path, output_width=size[0], output_height=size[1])
        img = Image.open(io.BytesIO(png_data))
        img.thumbnail(size, Image.LANCZOS)
        return img
    except Exception:
        pass

    # Method 3: svglib + reportlab
    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
        import io
        drawing = svg2rlg(file_path)
        if drawing:
            scale_x = size[0] / drawing.width if drawing.width else 1
            scale_y = size[1] / drawing.height if drawing.height else 1
            scale = min(scale_x, scale_y)
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
            png_data = renderPM.drawToString(drawing, fmt="PNG")
            img = Image.open(io.BytesIO(png_data))
            img.thumbnail(size, Image.LANCZOS)
            return img
    except Exception:
        pass

    # Method 4: Extract embedded base64 images from SVG <image> tags
    img = _extract_svg_embedded_image(file_path, size)
    if img:
        return img

    return None


def _extract_svg_embedded_image(file_path, size):
    """
    Extract embedded raster images from SVG file.
    Many SVGs (especially from Adobe Firefly/Illustrator) embed base64-encoded
    raster images in <image> tags with data: URIs.
    """
    import re
    import base64
    import io

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            svg_content = f.read()

        # Method A: Find data:image URIs in href/xlink:href attributes
        # Matches: href="data:image/png;base64,..." or xlink:href="data:image/jpeg;base64,..."
        data_uri_pattern = r'(?:href|xlink:href)\s*=\s*["\']data:image/(?:png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)["\']'
        matches = re.findall(data_uri_pattern, svg_content, re.DOTALL)

        if matches:
            # Use the largest embedded image (usually the main content)
            largest_data = max(matches, key=len)
            # Clean whitespace from base64 data
            clean_data = re.sub(r'\s+', '', largest_data)
            img_bytes = base64.b64decode(clean_data)
            if len(img_bytes) > 100:
                img = _try_load_image_data(img_bytes, size)
                if img:
                    return img

        # Method B: Also try to find raw JPEG/PNG bytes in the file (binary SVG)
        with open(file_path, 'rb') as f:
            raw_content = f.read()

        # Try JPEG
        jpeg_start = raw_content.find(b'\xff\xd8\xff')
        if jpeg_start >= 0:
            jpeg_end = raw_content.rfind(b'\xff\xd9')
            if jpeg_end > jpeg_start:
                jpeg_data = raw_content[jpeg_start:jpeg_end + 2]
                if len(jpeg_data) > 500:
                    img = _try_load_image_data(jpeg_data, size)
                    if img:
                        return img

        # Try PNG
        png_magic = b'\x89PNG\r\n\x1a\n'
        png_start = raw_content.find(png_magic)
        if png_start >= 0:
            png_end = raw_content.find(b'IEND', png_start)
            if png_end > png_start:
                png_data = raw_content[png_start:png_end + 8]
                if len(png_data) > 500:
                    img = _try_load_image_data(png_data, size)
                    if img:
                        return img

    except Exception:
        pass

    return None


def _try_render_eps(file_path, size):
    """Try to render EPS using Pillow (requires Ghostscript)."""
    try:
        img = Image.open(file_path)
        # Force immediate pixel rendering to catch Ghostscript errors here
        img.load()
        img.thumbnail(size, Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        pass
    return None


def _create_vector_placeholder(file_path, size, label):
    """Create a styled placeholder for vector files (SVG/EPS) that can't be rendered."""
    from PIL import ImageDraw, ImageFont

    # Color schemes for different vector types
    colors = {
        "SVG": {"bg": (40, 20, 80), "accent": (160, 100, 255), "text": (200, 180, 255)},
        "EPS": {"bg": (10, 35, 60), "accent": (0, 180, 240), "text": (150, 210, 255)},
    }
    scheme = colors.get(label, {"bg": (20, 25, 50), "accent": (0, 212, 255), "text": (180, 200, 240)})

    img = Image.new('RGB', size, color=scheme["bg"])
    draw = ImageDraw.Draw(img)

    # Draw accent border
    draw.rectangle([(0, 0), (size[0]-1, size[1]-1)], outline=scheme["accent"], width=1)

    # Draw label centered
    try:
        font_size = max(8, min(size[1] // 3, 16))
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    text = f".{label}"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size[0] - tw) // 2
    y = (size[1] - th) // 2
    draw.text((x, y), text, fill=scheme["text"], font=font)

    return img


def _create_placeholder(file_path, size, label):
    """Create a placeholder image for files that can't be previewed."""
    from PIL import ImageDraw, ImageFont
    img = Image.new('RGB', size, color=(20, 25, 60))
    draw = ImageDraw.Draw(img)
    ext = os.path.splitext(file_path)[1].upper()
    text = f"{label}\n{ext}"
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size[0] - text_w) // 2
    y = (size[1] - text_h) // 2
    draw.text((x, y), text, fill=(0, 212, 255), font=font)
    return img


def load_images_for_ai(file_path, file_type):
    """
    Load image(s) for AI analysis.
    
    For images: returns list with one image
    For vectors: tries to rasterize SVG/EPS, falls back to descriptive placeholder
    For videos: returns list of 5 frames
    
    Returns:
        List of PIL.Image objects
    """
    if file_type == "video":
        frames = extract_frames(file_path, num_frames=5)
        # Resize frames for API (max 1024px on longest side)
        resized = []
        for f in frames:
            f.thumbnail((1024, 1024), Image.LANCZOS)
            resized.append(f)
        return resized
    elif file_type == "vector":
        return _load_vector_for_ai(file_path)
    else:
        img = Image.open(file_path)
        img.thumbnail((1024, 1024), Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return [img]


def _load_vector_for_ai(file_path):
    """
    Load a vector file (SVG/EPS) as a rasterized image for AI analysis.
    Tries multiple rendering methods with fallback.
    """
    ext = os.path.splitext(file_path)[1].lower()
    size = (1024, 1024)

    if ext == '.svg':
        # Method 1: Wand (ImageMagick) — most reliable on Windows
        try:
            from wand.image import Image as WandImage
            import io
            with WandImage(filename=file_path, resolution=200) as wand_img:
                wand_img.format = 'png'
                wand_img.transform(resize=f'{size[0]}x{size[1]}>')
                png_blob = wand_img.make_blob('png')
            img = Image.open(io.BytesIO(png_blob))
            img.load()
            img.thumbnail(size, Image.LANCZOS)
            if img.mode != "RGB":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "RGBA":
                    bg.paste(img, mask=img.split()[3])
                else:
                    bg.paste(img)
                img = bg
            return [img]
        except Exception:
            pass

        # Method 2: cairosvg
        try:
            import cairosvg
            import io
            png_data = cairosvg.svg2png(url=file_path, output_width=size[0], output_height=size[1])
            img = Image.open(io.BytesIO(png_data))
            if img.mode != "RGB":
                img = img.convert("RGB")
            return [img]
        except Exception:
            pass

        # Method 2: svglib
        try:
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPM
            import io
            drawing = svg2rlg(file_path)
            if drawing:
                scale_x = size[0] / drawing.width if drawing.width else 1
                scale_y = size[1] / drawing.height if drawing.height else 1
                scale = min(scale_x, scale_y)
                drawing.width *= scale
                drawing.height *= scale
                drawing.scale(scale, scale)
                png_data = renderPM.drawToString(drawing, fmt="PNG")
                img = Image.open(io.BytesIO(png_data))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return [img]
        except Exception:
            pass

        # Method 3: Extract embedded base64/raster images from SVG
        embedded = _extract_svg_embedded_image(file_path, size)
        if embedded:
            if embedded.mode != "RGB":
                embedded = embedded.convert("RGB")
            return [embedded]

        # Method 4: Descriptive fallback image for AI
        return [_create_ai_vector_fallback(file_path, "SVG")]

    elif ext == '.eps':
        # Method 1: Extract embedded TIFF/WMF preview from EPS binary header
        # Most EPS files from Adobe tools contain an embedded preview image
        embedded_img = _extract_eps_preview(file_path, size)
        if embedded_img:
            return [embedded_img]

        # Method 2: Pillow (requires Ghostscript)
        try:
            img = Image.open(file_path)
            # Force immediate pixel loading to catch Ghostscript errors here
            img.load()
            img.thumbnail(size, Image.LANCZOS)
            if img.mode != "RGB":
                img = img.convert("RGB")
            # Verify we have actual pixel data
            _ = img.tobytes()
            return [img]
        except Exception:
            pass

        # Fallback: descriptive image for AI
        return [_create_ai_vector_fallback(file_path, "EPS")]

    # Unknown vector format
    return [_create_ai_vector_fallback(file_path, ext.upper().strip('.'))]


def _extract_eps_preview(file_path, size=(1024, 1024)):
    """
    Extract embedded preview image from EPS file.
    
    Tries multiple methods:
    1. DOS Binary Header (TIFF/WMF preview at known offsets)
    2. Embedded JPEG data (0xFF 0xD8 0xFF marker)
    3. Embedded PNG data (0x89 0x50 0x4E 0x47 marker)
    """
    import struct
    import io

    try:
        file_size = os.path.getsize(file_path)

        with open(file_path, 'rb') as f:
            header = f.read(30)

            # ── Method 1: DOS EPS Binary Header ────────────────────────
            if len(header) >= 28 and header[:4] == b'\xc5\xd0\xd3\xc6':
                tiff_offset = struct.unpack_from('<I', header, 20)[0]
                tiff_length = struct.unpack_from('<I', header, 24)[0]

                # Try TIFF preview (usually higher quality)
                if tiff_offset > 0 and tiff_length > 0 and tiff_offset + tiff_length <= file_size:
                    f.seek(tiff_offset)
                    tiff_data = f.read(tiff_length)
                    img = _try_load_image_data(tiff_data, size)
                    if img:
                        return img

                # Try WMF preview
                wmf_offset = struct.unpack_from('<I', header, 12)[0]
                wmf_length = struct.unpack_from('<I', header, 16)[0]
                if wmf_offset > 0 and wmf_length > 0 and wmf_offset + wmf_length <= file_size:
                    f.seek(wmf_offset)
                    wmf_data = f.read(wmf_length)
                    img = _try_load_image_data(wmf_data, size)
                    if img:
                        return img

            # ── Method 2: Scan for embedded raster data ────────────────
            # Read entire file content for scanning
            f.seek(0)
            # Limit read to 50MB to prevent memory issues
            content = f.read(min(file_size, 50 * 1024 * 1024))

            # Try JPEG (most common in EPS)
            jpeg_start = content.find(b'\xff\xd8\xff')
            if jpeg_start >= 0:
                # Find JPEG end marker - search from the end for the last one
                # (ensures we get complete JPEG data)
                jpeg_end = content.rfind(b'\xff\xd9')
                if jpeg_end > jpeg_start:
                    jpeg_data = content[jpeg_start:jpeg_end + 2]
                    if len(jpeg_data) > 500:  # Must be a real image, not noise
                        img = _try_load_image_data(jpeg_data, size)
                        if img:
                            return img

            # Try PNG
            png_magic = b'\x89PNG\r\n\x1a\n'
            png_start = content.find(png_magic)
            if png_start >= 0:
                # Find PNG end marker (IEND chunk)
                png_end = content.find(b'IEND', png_start)
                if png_end > png_start:
                    # IEND chunk is 4 bytes type + 4 bytes CRC after
                    png_data = content[png_start:png_end + 8]
                    if len(png_data) > 500:
                        img = _try_load_image_data(png_data, size)
                        if img:
                            return img

    except Exception:
        pass

    return None


def _try_load_image_data(data, size):
    """Try to load raw image data bytes as a PIL Image."""
    import io
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img.thumbnail(size, Image.LANCZOS)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception:
        return None


def _create_ai_vector_fallback(file_path, format_type):
    """
    Create a descriptive fallback image for AI when vector can't be rasterized.
    Includes file info and extracted text content to help AI generate metadata.
    """
    from PIL import ImageDraw, ImageFont

    img = Image.new('RGB', (800, 600), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("arial.ttf", 24)
        font_body = ImageFont.truetype("arial.ttf", 16)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = font_title
        font_small = font_title

    filename = os.path.basename(file_path)
    y = 30

    # Header
    draw.text((30, y), f"Vector File: {filename}", fill=(0, 0, 0), font=font_title)
    y += 40
    draw.text((30, y), f"Format: {format_type}", fill=(100, 100, 100), font=font_body)
    y += 30

    # Try to extract useful content from the file
    content_info = []
    try:
        if format_type == "SVG":
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                svg_content = f.read(10000)  # Read first 10KB

            # Extract title if present
            import re
            title_match = re.search(r'<title[^>]*>(.*?)</title>', svg_content, re.IGNORECASE)
            if title_match:
                content_info.append(f"SVG Title: {title_match.group(1)}")

            # Extract description if present
            desc_match = re.search(r'<desc[^>]*>(.*?)</desc>', svg_content, re.IGNORECASE)
            if desc_match:
                content_info.append(f"Description: {desc_match.group(1)}")

            # Extract text elements
            text_matches = re.findall(r'<text[^>]*>(.*?)</text>', svg_content, re.IGNORECASE | re.DOTALL)
            if text_matches:
                texts = [t.strip() for t in text_matches if t.strip()][:5]
                if texts:
                    content_info.append(f"Text elements: {', '.join(texts)}")

            # Count elements for description
            path_count = svg_content.count('<path')
            rect_count = svg_content.count('<rect')
            circle_count = svg_content.count('<circle')
            ellipse_count = svg_content.count('<ellipse')
            polygon_count = svg_content.count('<polygon')
            line_count = svg_content.count('<line')
            group_count = svg_content.count('<g ')
            img_count = svg_content.count('<image')

            elements = []
            if path_count: elements.append(f"{path_count} paths")
            if rect_count: elements.append(f"{rect_count} rectangles")
            if circle_count: elements.append(f"{circle_count} circles")
            if ellipse_count: elements.append(f"{ellipse_count} ellipses")
            if polygon_count: elements.append(f"{polygon_count} polygons")
            if line_count: elements.append(f"{line_count} lines")
            if group_count: elements.append(f"{group_count} groups")
            if img_count: elements.append(f"{img_count} images")

            if elements:
                content_info.append(f"Contains: {', '.join(elements)}")

            # Extract colors
            colors = re.findall(r'fill=["\']([^"\']+)["\']', svg_content)
            unique_colors = list(set(c for c in colors if c != 'none'))[:8]
            if unique_colors:
                content_info.append(f"Colors used: {', '.join(unique_colors)}")

            # Viewbox/dimensions
            viewbox = re.search(r'viewBox=["\']([^"\']+)["\']', svg_content)
            if viewbox:
                content_info.append(f"ViewBox: {viewbox.group(1)}")

        elif format_type == "EPS":
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                eps_header = f.read(2000)

            import re
            title_match = re.search(r'%%Title:\s*(.+)', eps_header)
            if title_match:
                content_info.append(f"EPS Title: {title_match.group(1).strip()}")

            creator_match = re.search(r'%%Creator:\s*(.+)', eps_header)
            if creator_match:
                content_info.append(f"Creator: {creator_match.group(1).strip()}")

            bbox_match = re.search(r'%%BoundingBox:\s*(.+)', eps_header)
            if bbox_match:
                content_info.append(f"BoundingBox: {bbox_match.group(1).strip()}")

    except Exception:
        pass

    # Draw filename context (helps AI understand the content)
    draw.line([(30, y), (770, y)], fill=(200, 200, 200), width=1)
    y += 15
    draw.text((30, y), "File Information:", fill=(50, 50, 50), font=font_body)
    y += 30

    # Draw extracted content info
    if content_info:
        for info in content_info:
            if y > 520:
                break
            # Truncate long lines
            if len(info) > 80:
                info = info[:77] + "..."
            draw.text((40, y), info, fill=(60, 60, 60), font=font_small)
            y += 20
    else:
        draw.text((40, y), "No extractable metadata found in file", fill=(150, 150, 150), font=font_small)
        y += 20

    y += 10
    draw.line([(30, y), (770, y)], fill=(200, 200, 200), width=1)
    y += 15

    # Note for AI
    note = f"Note: This is a {format_type} vector file. Please generate metadata based on the filename and extracted info above."
    draw.text((30, y), note, fill=(100, 100, 100), font=font_small)

    return img


def process_single_asset(asset, provider_name, model, api_key, on_log=None, custom_prompt="", platform="adobestock", ai_generated=False):
    """
    Process a single asset: load images, call AI, update database.
    
    Args:
        asset: Asset dict from database
        provider_name: AI provider name
        model: Model identifier
        api_key: API key
        on_log: Callback function for log messages (optional)
        custom_prompt: Custom keywords that must appear in title and keywords
        platform: "adobestock", "shutterstock", or "freepik"
        ai_generated: For Freepik, whether AI Generated checkbox is on
    
    Returns:
        dict with title, keywords, category or None on error
    """
    filename = asset["filename"]
    file_path = asset["file_path"]
    file_type = asset["file_type"]
    asset_id = asset["id"]

    try:
        if on_log:
            on_log(f"Loading {file_type}: {filename}...")

        # Load images for AI
        images = load_images_for_ai(file_path, file_type)

        if on_log:
            on_log(f"Sending to {provider_name} ({model})...")

        # Call AI
        result = generate_metadata(provider_name, model, api_key, images, filename, file_type,
                                   custom_prompt=custom_prompt, platform=platform, ai_generated=ai_generated)

        # Update database
        db.update_metadata(asset_id, result["title"], result["keywords"], result["category"])

        if on_log:
            on_log(f"✅ Done: {filename}")

        return result

    except Exception as e:
        db.update_status(asset_id, "error")
        if on_log:
            on_log(f"❌ Error ({filename}): {str(e)}")
        return None


def process_all_assets(assets, provider_name, model, api_key, stop_event, on_log=None, on_progress=None, on_asset_done=None, custom_prompt="", platform="adobestock", ai_generated=False):
    """
    Process all assets sequentially with stop support.
    
    Args:
        assets: List of asset dicts
        provider_name: AI provider name
        model: Model identifier
        api_key: API key
        stop_event: threading.Event to check for stop signal
        on_log: Callback for log messages
        on_progress: Callback for progress (current, total)
        on_asset_done: Callback when a single asset is done (asset_id, result)
        custom_prompt: Custom keywords that must appear in title and keywords
        platform: "adobestock", "shutterstock", or "freepik"
        ai_generated: For Freepik, whether AI Generated checkbox is on
    """
    total = len(assets)
    
    for i, asset in enumerate(assets):
        if stop_event.is_set():
            if on_log:
                on_log("⏹ Generation stopped by user.")
            break

        if on_log:
            on_log(f"[{i + 1}/{total}] Processing: {asset['filename']}...")

        if on_progress:
            on_progress(i + 1, total)

        result = process_single_asset(asset, provider_name, model, api_key, on_log,
                                      custom_prompt=custom_prompt, platform=platform, ai_generated=ai_generated)

        if on_asset_done:
            on_asset_done(asset["id"], result)

    if not stop_event.is_set():
        if on_log:
            on_log(f"🎉 All {total} assets processed successfully!")
        if on_progress:
            on_progress(total, total)

