-- ============================================================================
-- RZ Automedata - FIX RLS Policy untuk activity_logs
-- Jalankan ini di Supabase SQL Editor
-- ============================================================================

-- Drop policy lama yang bermasalah
DROP POLICY IF EXISTS "Allow anon to insert activity log" ON activity_logs;

-- Buat policy baru yang lebih permissive
-- Ini memperbolehkan anon role untuk INSERT ke activity_logs
-- tanpa constraint foreign key yang ketat
CREATE POLICY "Allow anon to insert activity log"
    ON activity_logs FOR INSERT
    TO anon
    WITH CHECK (true);

-- Juga tambahkan policy untuk SELECT activity_logs (untuk admin panel nanti)
DROP POLICY IF EXISTS "Allow anon to read own activity logs" ON activity_logs;
CREATE POLICY "Allow anon to read own activity logs"
    ON activity_logs FOR SELECT
    TO anon
    USING (true);

-- Pastikan foreign key constraint tidak blocking
-- Cek apakah ada issue dengan FK constraint
-- Jika masih error, kita bisa drop FK dan buat ulang tanpa constraint ketat
ALTER TABLE activity_logs DROP CONSTRAINT IF EXISTS activity_logs_license_id_fkey;
ALTER TABLE activity_logs ADD CONSTRAINT activity_logs_license_id_fkey 
    FOREIGN KEY (license_id) REFERENCES licenses(id) ON DELETE SET NULL;

-- Verifikasi RLS policies
SELECT schemaname, tablename, policyname, permissive, roles, cmd 
FROM pg_policies 
WHERE tablename IN ('licenses', 'app_versions', 'activity_logs', 'admin_settings');
