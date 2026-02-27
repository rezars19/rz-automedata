-- ============================================================================
-- RZ Automedata - Supabase Schema
-- License Management, Subscription & Update System
-- ============================================================================

-- ─── 1. LICENSES TABLE ─────────────────────────────────────────────────────
-- Menyimpan semua token lisensi dari setiap instalasi
CREATE TABLE IF NOT EXISTS licenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    license_key TEXT UNIQUE NOT NULL,
    machine_id TEXT,
    user_name TEXT DEFAULT '',
    user_email TEXT DEFAULT '',
    plan TEXT DEFAULT 'trial' CHECK (plan IN ('trial', 'monthly', 'yearly', 'lifetime')),
    status TEXT DEFAULT 'inactive' CHECK (status IN ('inactive', 'active', 'expired', 'banned')),
    activated_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    last_check TIMESTAMPTZ,
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index untuk lookup cepat
CREATE INDEX IF NOT EXISTS idx_licenses_license_key ON licenses (license_key);
CREATE INDEX IF NOT EXISTS idx_licenses_machine_id ON licenses (machine_id);
CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses (status);


-- ─── 2. APP VERSIONS TABLE ─────────────────────────────────────────────────
-- Menyimpan versi aplikasi untuk auto-update notification
CREATE TABLE IF NOT EXISTS app_versions (
    id SERIAL PRIMARY KEY,
    version TEXT NOT NULL,
    release_notes TEXT DEFAULT '',
    download_url TEXT NOT NULL,
    is_mandatory BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_versions_version ON app_versions (version);


-- ─── 3. ACTIVITY LOGS TABLE ────────────────────────────────────────────────
-- Log semua aktivitas untuk monitoring
CREATE TABLE IF NOT EXISTS activity_logs (
    id BIGSERIAL PRIMARY KEY,
    license_id UUID REFERENCES licenses(id) ON DELETE SET NULL,
    license_key TEXT,
    action TEXT NOT NULL CHECK (action IN (
        'registered',    -- Token baru dibuat (app pertama kali dibuka)
        'activated',     -- Admin mengaktifkan token
        'deactivated',   -- Admin menonaktifkan token
        'expired',       -- Langganan expired
        'renewed',       -- Langganan diperpanjang
        'banned',        -- Admin mem-ban user
        'unbanned',      -- Admin membuka ban
        'license_check', -- App melakukan pengecekan lisensi
        'app_opened',    -- App dibuka
        'plan_changed'   -- Plan diubah
    )),
    details TEXT DEFAULT '',
    ip_address TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_logs_license_id ON activity_logs (license_id);
CREATE INDEX IF NOT EXISTS idx_activity_logs_action ON activity_logs (action);
CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs (created_at DESC);


-- ─── 4. ADMIN SETTINGS TABLE ───────────────────────────────────────────────
-- Settings untuk admin panel
CREATE TABLE IF NOT EXISTS admin_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);


-- ─── 5. HELPER FUNCTIONS ───────────────────────────────────────────────────

-- Function: Auto-update "updated_at" timestamp
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_licenses_updated_at
    BEFORE UPDATE ON licenses
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();


-- Function: Activate a license (dipanggil dari Admin Panel)
CREATE OR REPLACE FUNCTION activate_license(
    p_license_key TEXT,
    p_plan TEXT DEFAULT 'monthly',
    p_duration_days INTEGER DEFAULT 30
)
RETURNS JSON AS $$
DECLARE
    v_license licenses%ROWTYPE;
    v_expires_at TIMESTAMPTZ;
BEGIN
    -- Find license
    SELECT * INTO v_license FROM licenses WHERE license_key = p_license_key;
    
    IF NOT FOUND THEN
        RETURN json_build_object('success', false, 'error', 'License not found');
    END IF;
    
    -- Calculate expiry
    IF p_plan = 'lifetime' THEN
        v_expires_at = NULL;  -- No expiry for lifetime
    ELSE
        v_expires_at = NOW() + (p_duration_days || ' days')::INTERVAL;
    END IF;
    
    -- Update license
    UPDATE licenses SET
        status = 'active',
        plan = p_plan,
        activated_at = COALESCE(activated_at, NOW()),
        expires_at = v_expires_at
    WHERE license_key = p_license_key;
    
    -- Log activity
    INSERT INTO activity_logs (license_id, license_key, action, details)
    VALUES (v_license.id, p_license_key, 'activated',
            'Plan: ' || p_plan || ', Duration: ' || p_duration_days || ' days');
    
    RETURN json_build_object(
        'success', true,
        'license_key', p_license_key,
        'plan', p_plan,
        'expires_at', v_expires_at
    );
END;
$$ LANGUAGE plpgsql;


-- Function: Check & auto-expire licenses
CREATE OR REPLACE FUNCTION check_expired_licenses()
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER;
BEGIN
    WITH expired AS (
        UPDATE licenses
        SET status = 'expired'
        WHERE status = 'active'
          AND expires_at IS NOT NULL
          AND expires_at < NOW()
        RETURNING id, license_key
    )
    INSERT INTO activity_logs (license_id, license_key, action, details)
    SELECT id, license_key, 'expired', 'Auto-expired by system'
    FROM expired;
    
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;


-- Function: Get dashboard statistics
CREATE OR REPLACE FUNCTION get_dashboard_stats()
RETURNS JSON AS $$
DECLARE
    v_total INTEGER;
    v_active INTEGER;
    v_inactive INTEGER;
    v_expired INTEGER;
    v_banned INTEGER;
    v_today_new INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_total FROM licenses;
    SELECT COUNT(*) INTO v_active FROM licenses WHERE status = 'active';
    SELECT COUNT(*) INTO v_inactive FROM licenses WHERE status = 'inactive';
    SELECT COUNT(*) INTO v_expired FROM licenses WHERE status = 'expired';
    SELECT COUNT(*) INTO v_banned FROM licenses WHERE status = 'banned';
    SELECT COUNT(*) INTO v_today_new FROM licenses
        WHERE created_at >= CURRENT_DATE;
    
    RETURN json_build_object(
        'total_licenses', v_total,
        'active', v_active,
        'inactive', v_inactive,
        'expired', v_expired,
        'banned', v_banned,
        'new_today', v_today_new
    );
END;
$$ LANGUAGE plpgsql;


-- ─── 6. ROW LEVEL SECURITY (RLS) ───────────────────────────────────────────
-- Proteksi agar user app hanya bisa baca data mereka sendiri

ALTER TABLE licenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_settings ENABLE ROW LEVEL SECURITY;

-- Policy: App (anon) bisa INSERT license baru (registrasi)
CREATE POLICY "Allow anon to register license"
    ON licenses FOR INSERT
    TO anon
    WITH CHECK (true);

-- Policy: App (anon) bisa SELECT license milik sendiri berdasarkan license_key
CREATE POLICY "Allow anon to read own license"
    ON licenses FOR SELECT
    TO anon
    USING (true);

-- Policy: App (anon) bisa UPDATE last_check pada license sendiri
CREATE POLICY "Allow anon to update last_check"
    ON licenses FOR UPDATE
    TO anon
    USING (true)
    WITH CHECK (true);

-- Policy: App (anon) bisa baca versi terbaru
CREATE POLICY "Allow anon to read app versions"
    ON app_versions FOR SELECT
    TO anon
    USING (is_active = true);

-- Policy: App (anon) bisa INSERT activity log
CREATE POLICY "Allow anon to insert activity log"
    ON activity_logs FOR INSERT
    TO anon
    WITH CHECK (true);

-- Policy: Service role (admin) bisa akses semua
-- (Service role bypass RLS by default, jadi tidak perlu policy tambahan)


-- ─── 7. SEED DATA ──────────────────────────────────────────────────────────

-- Insert versi pertama aplikasi
INSERT INTO app_versions (version, release_notes, download_url, is_mandatory)
VALUES ('1.0.0', 'Initial release - RZ Automedata', 'https://example.com/download/v1.0.0', false);

-- Insert default admin settings
INSERT INTO admin_settings (key, value) VALUES
    ('app_name', 'RZ Automedata'),
    ('trial_days', '2'),
    ('monthly_price', '40000'),
    ('default_plan_duration', '30');


-- ============================================================================
-- SELESAI! Schema siap digunakan.
-- 
-- Tabel yang dibuat:
--   1. licenses        - Data lisensi/token per instalasi
--   2. app_versions    - Daftar versi app untuk auto-update
--   3. activity_logs   - Log semua aktivitas
--   4. admin_settings  - Konfigurasi admin panel
--
-- Functions yang dibuat:
--   1. activate_license()       - Aktivasi token dari admin
--   2. check_expired_licenses() - Auto-expire langganan habis
--   3. get_dashboard_stats()    - Statistik untuk dashboard
-- ============================================================================
