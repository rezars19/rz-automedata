"""
RZ Automedata - CSV Exporter
Export metadata to Adobe Stock, Shutterstock, or Freepik CSV format.
"""

import csv
import os


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


def _export_adobestock_csv(assets, output_path):
    """Export assets metadata to Adobe Stock CSV format."""
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        # Adobe Stock CSV headers
        writer.writerow(["Filename", "Title", "Keywords", "Category"])
        
        for asset in assets:
            writer.writerow([
                asset.get("filename", ""),
                asset.get("title", ""),
                asset.get("keywords", ""),
                asset.get("category", "")
            ])
    
    return output_path


def _export_shutterstock_csv(assets, output_path):
    """Export assets metadata to Shutterstock CSV format."""
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile)
        # Shutterstock CSV headers
        writer.writerow([
            "Filename", "Description", "Keywords", "Categories",
            "Editorial", "Mature content", "illustration"
        ])
        
        for asset in assets:
            # Category is stored as "Cat1,Cat2" format
            # For CSV, use only the first part before "/" (e.g. "Backgrounds/Textures" → "Backgrounds")
            raw_category = asset.get("category", "")
            cats = [c.strip().split("/")[0] for c in raw_category.split(",") if c.strip()]
            category = ",".join(cats)
            
            writer.writerow([
                asset.get("filename", ""),
                asset.get("title", ""),       # description = title field
                asset.get("keywords", ""),
                category,
                "no",                          # Editorial
                "no",                          # Mature content
                "no"                           # illustration
            ])
    
    return output_path


def _csv_cell(value):
    """Escape a value for semicolon-delimited CSV.
    
    Wraps value in double-quotes if it contains semicolons, quotes, or newlines.
    Internal double-quotes are doubled ("") per CSV convention.
    """
    s = str(value).strip()
    if ';' in s or '"' in s or '\n' in s or '\r' in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def _export_freepik_csv(assets, output_path):
    """Export assets metadata to Freepik CSV format.
    
    Freepik expects:
    - Semicolon (;) as delimiter
    - Plain UTF-8 encoding (no BOM)
    - No sep= hint line
    - Columns: Filename;Title;Keywords;Prompt;Model
    """
    header = ['Filename', 'Title', 'Keywords', 'Prompt', 'Model']

    with open(output_path, 'w', encoding='utf-8', newline='') as csvfile:
        # Write header
        csvfile.write(';'.join(header) + '\n')

        for asset in assets:
            filename = _csv_cell(asset.get("filename", ""))
            title    = _csv_cell(asset.get("title", ""))
            keywords = _csv_cell(asset.get("keywords", ""))
            prompt   = _csv_cell(asset.get("prompt", ""))
            model    = _csv_cell(asset.get("model", ""))

            csvfile.write(';'.join([filename, title, keywords, prompt, model]) + '\n')

    return output_path
