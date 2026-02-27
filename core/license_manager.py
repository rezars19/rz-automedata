"""
RZ Automedata - License Manager
Handles license registration, validation, and update checking via Supabase.

Flow:
  1. App dibuka → register_or_load_license()
     - Jika belum pernah install → generate token, simpan ke Supabase (trial 2 hari)
     - Jika sudah pernah → load token dari file lokal
  2. check_license() → validasi ke Supabase
     - Cek status (active/inactive/expired/banned)
     - Cek expiry date
     - Offline grace period 3 hari
  3. check_for_updates() → cek versi terbaru dari Supabase
"""

import uuid
import hashlib
import platform
import subprocess
import os
import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────
# PENTING: Ganti ini dengan URL dan Key dari Supabase project kamu!
SUPABASE_URL = "https://wezzprtpainlmtqxoepb.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndlenpwcnRwYWlubG10cXhvZXBiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwODAyNTYsImV4cCI6MjA4NjY1NjI1Nn0.r6KHePgzAeBlmxGi10zyLe_6kxwiYRJEuJMxe3IOv8M"

# App version — update setiap kali build EXE baru
CURRENT_VERSION = "1.2.5"

# Trial duration
TRIAL_DAYS = 2

# Offline grace period (hari) — app tetap bisa dipakai jika tidak bisa konek
OFFLINE_GRACE_DAYS = 3

# Path file lisensi lokal (di folder AppData user)
LICENSE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RZAutomedata")
LICENSE_FILE = os.path.join(LICENSE_DIR, "license.json")
OFFLINE_CACHE_FILE = os.path.join(LICENSE_DIR, "cache.json")


# ─── Supabase Client ────────────────────────────────────────────────────────

_supabase_client = None

def _get_supabase():
    """Get or create Supabase client (lazy init)."""
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            raise ConnectionError(
                "Supabase belum dikonfigurasi. "
                "Set SUPABASE_URL dan SUPABASE_ANON_KEY di license_manager.py"
            )
        from supabase import create_client
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _supabase_client


# ─── Machine ID ─────────────────────────────────────────────────────────────

def get_machine_id():
    """
    Generate unique machine ID based on hardware.
    Digunakan untuk binding token ke PC tertentu.
    """
    try:
        if platform.system() == "Windows":
            # Ambil UUID dari motherboard
            output = subprocess.check_output(
                "wmic csproduct get uuid",
                shell=True, stderr=subprocess.DEVNULL
            ).decode().strip()
            # Parse output — ambil baris terakhir yang bukan header
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            hw_id = lines[-1] if len(lines) > 1 else lines[0]
        elif platform.system() == "Darwin":
            # macOS — serial number
            output = subprocess.check_output(
                "ioreg -rd1 -c IOPlatformExpertDevice | grep IOPlatformSerialNumber",
                shell=True, stderr=subprocess.DEVNULL
            ).decode().strip()
            hw_id = output.split('"')[-2] if '"' in output else str(uuid.getnode())
        else:
            # Linux — machine-id
            if os.path.exists("/etc/machine-id"):
                with open("/etc/machine-id", "r") as f:
                    hw_id = f.read().strip()
            else:
                hw_id = str(uuid.getnode())

        return hashlib.sha256(hw_id.encode()).hexdigest()[:32]

    except Exception as e:
        logger.warning(f"Failed to get hardware ID, using MAC address: {e}")
        return hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:32]


# ─── Local License File ─────────────────────────────────────────────────────

def _ensure_license_dir():
    """Pastikan folder license ada."""
    os.makedirs(LICENSE_DIR, exist_ok=True)


def _save_local_license(license_key, machine_id):
    """Simpan license key ke file lokal."""
    _ensure_license_dir()
    data = {
        "license_key": license_key,
        "machine_id": machine_id,
        "installed_at": datetime.now(timezone.utc).isoformat()
    }
    with open(LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(f"License saved locally: {license_key[:8]}...")


def _load_local_license():
    """Load license key dari file lokal. Returns dict or None."""
    if not os.path.exists(LICENSE_FILE):
        return None
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load local license: {e}")
        return None


def _save_offline_cache(license_data):
    """Cache license data untuk offline mode."""
    _ensure_license_dir()
    cache = {
        "license_data": license_data,
        "cached_at": datetime.now(timezone.utc).isoformat()
    }
    with open(OFFLINE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def _load_offline_cache():
    """Load cached license data. Returns (license_data, cached_at) or (None, None)."""
    if not os.path.exists(OFFLINE_CACHE_FILE):
        return None, None
    try:
        with open(OFFLINE_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        cached_at = datetime.fromisoformat(cache["cached_at"])
        return cache["license_data"], cached_at
    except Exception:
        return None, None


# ─── Registration ────────────────────────────────────────────────────────────

def register_or_load_license():
    """
    Register instalasi baru atau load token yang sudah ada.
    
    ANTI-ABUSE: Jika user uninstall & install ulang, machine_id tetap sama
    (berbasis hardware). App akan cek Supabase by machine_id dulu,
    sehingga tidak bisa dapat trial baru.
    
    Returns:
        str: License key
    """
    # 1. Cek file lokal dulu (paling cepat)
    local = _load_local_license()
    if local and local.get("license_key"):
        logger.info(f"Existing license found: {local['license_key'][:8]}...")
        return local["license_key"]

    # 2. File lokal tidak ada (fresh install / reinstall)
    #    → Cek Supabase by machine_id untuk mencegah trial abuse
    machine_id = get_machine_id()

    try:
        supabase = _get_supabase()

        # Cek apakah machine_id ini sudah pernah terdaftar
        existing = supabase.table("licenses").select("*").eq(
            "machine_id", machine_id
        ).order("created_at", desc=False).limit(1).execute()

        if existing.data:
            # ── MESIN INI SUDAH PERNAH TERDAFTAR ──
            # Restore license yang lama (termasuk status expired-nya)
            # TIDAK beri trial baru!
            license_data = existing.data[0]
            license_key = license_data["license_key"]
            _save_local_license(license_key, machine_id)
            logger.info(f"Restored existing license for this machine: {license_key[:8]}... "
                        f"(status: {license_data.get('status', 'unknown')})")
            return license_key

    except Exception as e:
        logger.warning(f"Failed to check existing machine license: {e}")

    # 3. Benar-benar mesin baru → buat trial baru
    license_key = _generate_license_key()

    try:
        supabase = _get_supabase()

        # Hitung trial expiry (2 hari dari sekarang)
        now = datetime.now(timezone.utc)
        trial_expires = now + timedelta(days=TRIAL_DAYS)

        # Insert ke Supabase
        result = supabase.table("licenses").insert({
            "license_key": license_key,
            "machine_id": machine_id,
            "status": "active",         # Trial langsung aktif
            "plan": "trial",
            "activated_at": now.isoformat(),
            "expires_at": trial_expires.isoformat(),
        }).execute()

        # Log activity (separate try-except so it doesn't break registration)
        try:
            license_id = result.data[0]["id"] if result.data else None
            supabase.table("activity_logs").insert({
                "license_key": license_key,
                "license_id": license_id,
                "action": "registered",
                "details": f"New installation. Trial {TRIAL_DAYS} days until {trial_expires.strftime('%Y-%m-%d %H:%M')}"
            }).execute()
        except Exception as log_err:
            logger.warning(f"Failed to log registration activity: {log_err}")

        logger.info(f"New license registered: {license_key[:8]}... (trial {TRIAL_DAYS} days)")

    except Exception as e:
        logger.error(f"Failed to register license online: {e}")

    # Simpan ke file lokal
    _save_local_license(license_key, machine_id)
    return license_key


def _generate_license_key():
    """Generate unique license key format: XXXX-XXXX-XXXX-XXXX."""
    raw = uuid.uuid4().hex.upper()
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


# ─── License Validation ─────────────────────────────────────────────────────

def check_license():
    """
    Validasi lisensi ke Supabase.
    
    Returns:
        tuple: (is_valid: bool, result: dict|str)
            - is_valid=True  → result berisi license data dict
            - is_valid=False → result berisi pesan error string
    """
    local = _load_local_license()
    if not local:
        return False, "Lisensi tidak ditemukan. Silakan install ulang aplikasi."

    license_key = local["license_key"]
    machine_id = get_machine_id()

    try:
        supabase = _get_supabase()

        # Query license dari Supabase
        result = supabase.table("licenses").select("*").eq(
            "license_key", license_key
        ).execute()

        if not result.data:
            return False, "Lisensi tidak terdaftar di server."

        license_data = result.data[0]

        # ── Cek machine_id cocok ──
        if license_data.get("machine_id") and license_data["machine_id"] != machine_id:
            return False, "Lisensi ini terdaftar di perangkat lain.\nHubungi admin untuk reset."

        # ── Cek status ──
        status = license_data.get("status", "inactive")

        if status == "banned":
            return False, "Akun Anda telah di-banned.\nHubungi admin."

        if status == "inactive":
            return False, (
                "Lisensi belum diaktifkan.\n"
                "Kirim token berikut ke admin:\n\n"
                f"{license_key}"
            )

        if status == "expired":
            return False, (
                "Langganan Anda telah berakhir.\n"
                "Hubungi admin untuk perpanjang."
            )

        # ── Cek expiry date (skip untuk plan lifetime) ──
        plan = license_data.get("plan", "trial")
        expires_at_str = license_data.get("expires_at")

        if plan != "lifetime" and expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)

            if now > expires_at:
                # Update status ke expired
                supabase.table("licenses").update({
                    "status": "expired"
                }).eq("license_key", license_key).execute()

                # Log
                supabase.table("activity_logs").insert({
                    "license_key": license_key,
                    "license_id": license_data["id"],
                    "action": "expired",
                    "details": f"Expired at {expires_at.strftime('%Y-%m-%d %H:%M')}"
                }).execute()

                if plan == "trial":
                    return False, (
                        "Masa trial 2 hari telah berakhir.\n"
                        "Hubungi admin untuk berlangganan."
                    )
                else:
                    return False, (
                        "Langganan Anda telah berakhir.\n"
                        "Hubungi admin untuk perpanjang."
                    )

        # ── License valid! ──
        # Update last_check
        supabase.table("licenses").update({
            "last_check": datetime.now(timezone.utc).isoformat()
        }).eq("license_key", license_key).execute()

        # Cache untuk offline mode
        _save_offline_cache(license_data)

        # Hitung sisa hari
        days_left = None
        if plan == "lifetime":
            days_left = "∞"
        elif expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            days_left = (expires_at - datetime.now(timezone.utc)).days

        license_data["days_left"] = days_left
        return True, license_data

    except Exception as e:
        logger.warning(f"Online license check failed: {e}")
        # ── Offline fallback ──
        return _check_offline_cache()


def _check_offline_cache():
    """
    Fallback: cek dari cache lokal jika tidak bisa konek ke Supabase.
    Grace period: OFFLINE_GRACE_DAYS hari.
    """
    cached_data, cached_at = _load_offline_cache()

    if not cached_data or not cached_at:
        return False, (
            "Tidak dapat terhubung ke server.\n"
            "Pastikan koneksi internet aktif."
        )

    # Cek apakah masih dalam grace period
    now = datetime.now(timezone.utc)
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)

    days_offline = (now - cached_at).days

    if days_offline > OFFLINE_GRACE_DAYS:
        return False, (
            f"Anda sudah offline selama {days_offline} hari.\n"
            "Koneksikan ke internet untuk validasi lisensi."
        )

    # Masih dalam grace period
    if cached_data.get("status") != "active":
        return False, "Lisensi tidak aktif."

    cached_data["days_left"] = None
    cached_data["offline_mode"] = True
    cached_data["offline_days"] = days_offline
    logger.info(f"Offline mode: {days_offline}/{OFFLINE_GRACE_DAYS} days grace period")
    return True, cached_data


# ─── Update Checker ─────────────────────────────────────────────────────────

def check_for_updates():
    """
    Cek apakah ada versi baru di Supabase.
    
    Returns:
        dict or None: Info update jika ada, None jika sudah terbaru.
            {
                "version": "2.0.0",
                "release_notes": "...",
                "download_url": "...",
                "is_mandatory": True/False
            }
    """
    try:
        from packaging.version import Version
    except ImportError:
        # Fallback jika packaging tidak terinstall
        logger.warning("packaging module not found, skipping update check")
        return None

    try:
        supabase = _get_supabase()

        result = supabase.table("app_versions").select("*").eq(
            "is_active", True
        ).order(
            "created_at", desc=True
        ).limit(1).execute()

        if not result.data:
            return None

        latest = result.data[0]
        latest_version = latest.get("version", "0.0.0")

        if Version(latest_version) > Version(CURRENT_VERSION):
            return {
                "version": latest_version,
                "release_notes": latest.get("release_notes", ""),
                "download_url": latest.get("download_url", ""),
                "is_mandatory": latest.get("is_mandatory", False)
            }

        return None  # Sudah versi terbaru

    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        return None


# ─── Utility Functions ───────────────────────────────────────────────────────

def get_license_info():
    """
    Get license info ringkas untuk ditampilkan di UI.
    
    Returns:
        dict: {
            "license_key": "XXXX-XXXX-...",
            "plan": "trial" / "monthly",
            "days_left": 15 or None,
            "status": "active" / "expired" / ...
        }
    """
    local = _load_local_license()
    if not local:
        return {"license_key": "N/A", "plan": "N/A", "days_left": None, "status": "unknown"}

    is_valid, result = check_license()
    if is_valid and isinstance(result, dict):
        return {
            "license_key": local["license_key"],
            "plan": result.get("plan", "unknown"),
            "days_left": result.get("days_left"),
            "status": result.get("status", "unknown"),
            "offline_mode": result.get("offline_mode", False)
        }
    else:
        return {
            "license_key": local["license_key"],
            "plan": "N/A",
            "days_left": None,
            "status": "invalid"
        }


def get_current_version():
    """Return current app version string."""
    return CURRENT_VERSION


def is_configured():
    """Check apakah Supabase sudah dikonfigurasi."""
    return bool(SUPABASE_URL) and bool(SUPABASE_ANON_KEY)
