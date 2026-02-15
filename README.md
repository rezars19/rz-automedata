# ⚡ RZ Automedata

**Stock Metadata Generator** — Desktop application for generating AI-powered metadata for Adobe Stock, Shutterstock, and Freepik.

## ✨ Features

- 🤖 **AI-Powered Metadata** — Generate titles, keywords, and categories using Gemini, Groq, OpenRouter, or Maia Router
- 🖼️ **Multi-Format Support** — Images (JPG, PNG), Vectors (EPS, SVG), and Videos (MP4, MOV)
- 🎯 **Multi-Platform** — Adobe Stock, Shutterstock, and Freepik metadata formats
- 📥 **CSV Export** — Download ready-to-upload CSV files
- 🎬 **Video Analysis** — Extracts 5 frames for comprehensive video understanding
- 📂 **Drag & Drop** — Simply drag files into the app
- 🔑 **License Management** — Secure Supabase-based licensing
- 🔄 **Auto Updates** — Automatic update notifications and downloads
- 🌙 **Blue Neon Theme** — Beautiful dark theme UI

## 🚀 Quick Start

### For Users (EXE)

1. Download `RZAutomedata.exe` from [Releases](../../releases)
2. Run the app
3. Enter your API key in Settings
4. Add assets and generate metadata!

### For Developers

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py

# Build to exe
python build.py
```

## 📦 Requirements

- Python 3.10+
- See `requirements.txt` for full dependency list

## 🏗️ Project Structure

```
├── app.py                  # Main application (UI)
├── ai_providers.py         # AI provider integrations
├── metadata_processor.py   # Asset processing logic
├── csv_exporter.py         # CSV export for all platforms
├── database.py             # Local SQLite database
├── license_manager.py      # License & update management
├── auto_updater.py         # Auto-update system
├── video_utils.py          # Video frame extraction
├── build.py                # Build script (PyInstaller)
├── admin_panel.html        # Admin dashboard (web)
└── requirements.txt        # Python dependencies
```

## 📋 License

Proprietary — All rights reserved.
