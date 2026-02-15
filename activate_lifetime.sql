-- ═══════════════════════════════════════════════════════════════
-- AKTIFKAN LIFETIME UNTUK MACHINE KAMU
-- Jalankan di: Supabase Dashboard → SQL Editor
-- ═══════════════════════════════════════════════════════════════

-- 1. Lihat semua license (untuk memastikan data ada)
SELECT id, license_key, machine_id, status, plan, expires_at, created_at 
FROM licenses 
ORDER BY created_at ASC;

-- 2. Hapus duplicates (sisakan 1 license per machine_id - yang paling pertama)
DELETE FROM licenses 
WHERE id NOT IN (
    SELECT DISTINCT ON (machine_id) id 
    FROM licenses 
    ORDER BY machine_id, created_at ASC
);

-- 3. Aktifkan lifetime untuk machine kamu
UPDATE licenses 
SET status = 'active', 
    plan = 'lifetime', 
    expires_at = NULL,
    activated_at = NOW()
WHERE machine_id = '517c0a8d34584b4a14a9feac2ffab9d5';

-- 4. Verifikasi
SELECT id, license_key, machine_id, status, plan, expires_at 
FROM licenses 
WHERE machine_id = '517c0a8d34584b4a14a9feac2ffab9d5';

-- 5. Tambah RLS policy agar app bisa UPDATE license (untuk last_check)  
DO $$
BEGIN
    -- Policy untuk SELECT by license_key (sudah ada, skip jika error)
    BEGIN
        CREATE POLICY "Allow anon to read own license"
            ON licenses FOR SELECT
            TO anon
            USING (true);
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
    
    -- Policy untuk UPDATE (untuk update last_check)
    BEGIN
        CREATE POLICY "Allow anon to update own license"
            ON licenses FOR UPDATE
            TO anon
            USING (true)
            WITH CHECK (true);
    EXCEPTION WHEN duplicate_object THEN NULL;
    END;
END $$;
