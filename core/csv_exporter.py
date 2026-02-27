"""
RZ Automedata - CSV Exporter
Export metadata to Adobe Stock, Shutterstock, or Freepik CSV format.

Quoting rules per platform:
  Adobe Stock:  Filename (plain), Title (quoted), Keywords (quoted), Category (plain)
  Shutterstock: Filename (plain), Description (quoted), Keywords (quoted),
                Categories (quoted), Editorial/Mature/illustration (plain)
  Freepik:      Semicolon-delimited, all text fields quoted
"""

import os


def _sanitize_field(value):
    """Clean a field value for CSV export.

    Removes leading/trailing whitespace, normalizes internal whitespace,
    removes any stray newlines that could break CSV rows.
    """
    if value is None:
        return ""
    s = str(value).strip()
    # Replace newlines/carriage-returns with spaces (they break CSV rows)
    s = s.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
    # Collapse multiple spaces
    while '  ' in s:
        s = s.replace('  ', ' ')
    return s.strip()


def _sanitize_keywords(keywords_str):
    """Normalize keywords: trim each keyword, remove empty ones, rejoin.

    Ensures all keywords from the UI survive into the CSV without being
    silently dropped by whitespace issues.
    """
    if not keywords_str:
        return ""
    # Split on comma, strip each keyword, filter out empty ones
    kw_list = [kw.strip() for kw in str(keywords_str).split(",") if kw.strip()]
    return ", ".join(kw_list)


def export_csv(assets, output_path, platform="adobestock"):
    """
    Export assets metadata to CSV format based on the selected platform.

    Args:
        assets: List of asset dicts with keys: filename, title, keywords, category
                For freepik, also: prompt, model
        output_path: Path to save the CSV file
        platform: "adobestock", "shutterstock", or "freepik"

    Returns:
        Path to the saved CSV file
    """
    if platform == "freepik":
        return _export_freepik_csv(assets, output_path)
    elif platform == "shutterstock":
        return _export_shutterstock_csv(assets, output_path)
    else:
        return _export_adobestock_csv(assets, output_path)


def _quote(value):
    """Wrap a value in double-quotes, escaping internal quotes per CSV convention."""
    s = str(value)
    return '"' + s.replace('"', '""') + '"'


def _export_adobestock_csv(assets, output_path):
    """Export assets metadata to Adobe Stock CSV format.

    Adobe Stock expects:
    - Comma (,) as delimiter
    - UTF-8 BOM encoding
    - Filename: unquoted
    - Title: quoted
    - Keywords: quoted
    - Category: unquoted (number)

    Example row:
    bg1_compress.mov,"Vibrant, flowing gradient of soft pastel colors...","gradient, abstract, ...",8
    """
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write('Filename,Title,Keywords,Category\n')

        for asset in assets:
            filename = _sanitize_field(asset.get("filename", ""))
            title = _sanitize_field(asset.get("title", ""))
            keywords = _sanitize_keywords(asset.get("keywords", ""))
            category = _sanitize_field(asset.get("category", ""))

            if not filename:
                continue

            # Filename: plain, Title: quoted, Keywords: quoted, Category: plain
            f.write(f'{filename},{_quote(title)},{_quote(keywords)},{category}\n')

    return output_path


def _export_shutterstock_csv(assets, output_path):
    """Export assets metadata to Shutterstock CSV format.

    Shutterstock expects:
    - Comma (,) as delimiter
    - UTF-8 BOM encoding
    - Filename: unquoted
    - Description: quoted
    - Keywords: quoted
    - Categories: quoted (contains comma, e.g. "Objects,Backgrounds")
    - Editorial: unquoted
    - Mature content: unquoted
    - illustration: unquoted

    Example row:
    bg18.mov,"Description here...","keyword1, keyword2...","Objects,Backgrounds",no,no,no
    """
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write('Filename,Description,Keywords,Categories,Editorial,Mature content,illustration\n')

        for asset in assets:
            filename = _sanitize_field(asset.get("filename", ""))
            description = _sanitize_field(asset.get("title", ""))
            keywords = _sanitize_keywords(asset.get("keywords", ""))

            # Category is stored as "Cat1,Cat2" format — strip subcategory after /
            raw_category = _sanitize_field(asset.get("category", ""))
            cats = [c.strip().split("/")[0] for c in raw_category.split(",") if c.strip()]
            category = ",".join(cats)

            if not filename:
                continue

            # Filename: plain, Description: quoted, Keywords: quoted,
            # Categories: quoted, Editorial/Mature/illustration: plain
            f.write(f'{filename},{_quote(description)},{_quote(keywords)},{_quote(category)},no,no,no\n')

    return output_path


def _csv_cell(value):
    """Escape a value for semicolon-delimited CSV (Freepik).

    Wraps value in double-quotes if it contains semicolons, quotes, or newlines.
    Internal double-quotes are doubled ("") per CSV convention.
    Always quotes non-empty values to be safe.
    """
    s = _sanitize_field(value)
    if not s:
        return '""'
    # Always quote to prevent delimiter issues
    s = '"' + s.replace('"', '""') + '"'
    return s


def _export_freepik_csv(assets, output_path):
    """Export assets metadata to Freepik CSV format.

    Freepik expects:
    - Semicolon (;) as delimiter
    - Plain UTF-8 encoding (no BOM)
    - No sep= hint line
    - Columns: Filename;Title;Keywords;Prompt;Model
    - All text fields quoted
    """
    header = ['Filename', 'Title', 'Keywords', 'Prompt', 'Model']

    with open(output_path, 'w', encoding='utf-8', newline='') as csvfile:
        # Write header
        csvfile.write(';'.join(header) + '\n')

        for asset in assets:
            filename = _csv_cell(asset.get("filename", ""))
            title    = _csv_cell(asset.get("title", ""))
            # Sanitize keywords separately to preserve all keywords
            keywords = _csv_cell(_sanitize_keywords(asset.get("keywords", "")))
            prompt   = _csv_cell(asset.get("prompt", ""))
            model    = _csv_cell(asset.get("model", ""))

            # Skip rows with no filename
            raw_fn = _sanitize_field(asset.get("filename", ""))
            if not raw_fn:
                continue

            csvfile.write(';'.join([filename, title, keywords, prompt, model]) + '\n')

    return output_path
