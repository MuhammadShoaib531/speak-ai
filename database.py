import psycopg
import os
from dotenv import load_dotenv
from contextlib import contextmanager

# Load environment variables
load_dotenv()

# Database configuration
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_connection():
    """Get database connection"""
    print(f"Attempting to connect to: {DB_HOST}:{5432}")
    print(f"Database: {DB_NAME}, User: {DB_USER}")
    print(f"Password set: {'Yes' if DB_PASSWORD and DB_PASSWORD != 'your_password_here' else 'No (using placeholder)'}")
    
    try:
        return psycopg.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME,
            port=5432,
            sslmode="require",
            connect_timeout=30
        )
    except Exception as e:
        print(f"Database connection failed: {e}")
        raise

@contextmanager
def get_db():
    """Database connection context manager"""
    conn = None
    try:
        conn = get_connection()
        yield conn
    finally:
        if conn:
            conn.close()

def create_tables():
    """Create database tables"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                name VARCHAR(255) NOT NULL,
                company_name VARCHAR(255) NOT NULL,
                hashed_password VARCHAR(255) NOT NULL,
                role VARCHAR(50) DEFAULT 'Admin',
                is_active BOOLEAN DEFAULT TRUE,
                is_verified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add role column to existing users table if it doesn't exist
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'users' AND column_name = 'role'
                ) THEN
                    ALTER TABLE users ADD COLUMN role VARCHAR(50) DEFAULT 'Admin';
                    UPDATE users SET role = 'Admin' WHERE role IS NULL;
                END IF;
            END $$;
        """)

        # Create agents table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                agent_id VARCHAR(255) NOT NULL,
                agent_name VARCHAR(255) NOT NULL,
                first_message TEXT NOT NULL,
                prompt TEXT NOT NULL,
                llm VARCHAR(255) NOT NULL,
                documentation_id VARCHAR(255),
                file_name VARCHAR(255),
                file_url TEXT,
                voice_id VARCHAR(255),
                twilio_number VARCHAR(20) NOT NULL,
                business_name VARCHAR(255),
                agent_type VARCHAR(255),
                speaking_style VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create trigger for updated_at
        cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ language 'plpgsql';
        """)
        
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgname = 'update_users_updated_at'
                ) THEN
                    CREATE TRIGGER update_users_updated_at
                        BEFORE UPDATE ON users
                        FOR EACH ROW
                        EXECUTE FUNCTION update_updated_at_column();
                END IF;
            END $$;
        """)

        # Add trigger for agents table
        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger WHERE tgname = 'update_agents_updated_at'
                ) THEN
                    CREATE TRIGGER update_agents_updated_at
                        BEFORE UPDATE ON agents
                        FOR EACH ROW
                        EXECUTE FUNCTION update_updated_at_column();
                END IF;
            END $$;
        """)
        
        conn.commit()
        print("Tables created successfully!") 