-- Migration script to create batch_calls table for tracking ElevenLabs batch calling jobs
-- Run this SQL script in your PostgreSQL database

CREATE TABLE IF NOT EXISTS batch_calls (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,
    batch_job_id VARCHAR(255) NOT NULL UNIQUE,
    call_name VARCHAR(255) NOT NULL,
    total_numbers INTEGER NOT NULL,
    scheduled_time_unix BIGINT,
    status VARCHAR(50) DEFAULT 'submitted',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_batch_calls_user_id ON batch_calls(user_id);
CREATE INDEX IF NOT EXISTS idx_batch_calls_agent_id ON batch_calls(agent_id);
CREATE INDEX IF NOT EXISTS idx_batch_calls_batch_job_id ON batch_calls(batch_job_id);
CREATE INDEX IF NOT EXISTS idx_batch_calls_created_at ON batch_calls(created_at DESC);

-- Add comments for documentation
COMMENT ON TABLE batch_calls IS 'Tracks ElevenLabs batch calling jobs submitted by users';
COMMENT ON COLUMN batch_calls.batch_job_id IS 'ElevenLabs batch calling job ID returned from their API';
COMMENT ON COLUMN batch_calls.call_name IS 'User-defined name for the batch calling job';
COMMENT ON COLUMN batch_calls.scheduled_time_unix IS 'Unix timestamp for scheduled calls (NULL for immediate calls)';
COMMENT ON COLUMN batch_calls.status IS 'Local tracking status: submitted, completed, failed, etc.';
