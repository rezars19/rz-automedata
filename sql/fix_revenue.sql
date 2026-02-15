-- ============================================================
-- FIX REVENUE: Add revenue tracking to activity_logs
-- ============================================================

-- 1. Add revenue_amount column to activity_logs
ALTER TABLE activity_logs 
ADD COLUMN IF NOT EXISTS revenue_amount INTEGER DEFAULT 0;

-- 2. Reset test data: Delete all test activation logs
-- (This will reset revenue to 0)
DELETE FROM activity_logs WHERE action = 'activated';

-- 3. Verify the column was added
SELECT column_name, data_type, column_default 
FROM information_schema.columns 
WHERE table_name = 'activity_logs' AND column_name = 'revenue_amount';
