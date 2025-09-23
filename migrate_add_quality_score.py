#!/usr/bin/env python3
"""
Database migration script to add quality_score column to mixing_sessions table.
Run this script to update your existing database with the new quality_score field.
"""

import sqlite3
import os
from pathlib import Path

def migrate_database():
    # Get the database path
    db_path = os.path.join(os.path.dirname(__file__), 'instance', 'app.db')
    
    # Check if database exists
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        print("Please run the Flask app first to create the database, then run this migration.")
        return
    
    print(f"Found database at {db_path}")
    
    # Connect to the database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if quality_score column already exists
        cursor.execute("PRAGMA table_info(mixing_sessions)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'quality_score' in columns:
            print("quality_score column already exists. Migration not needed.")
            return
        
        print("Adding quality_score column to mixing_sessions table...")
        
        # Add the quality_score column
        cursor.execute("""
            ALTER TABLE mixing_sessions 
            ADD COLUMN quality_score INTEGER
        """)
        
        # Commit the changes
        conn.commit()
        print("✅ Successfully added quality_score column to mixing_sessions table")
        
        # Verify the column was added
        cursor.execute("PRAGMA table_info(mixing_sessions)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'quality_score' in columns:
            print("✅ Verification successful: quality_score column is now present")
        else:
            print("❌ Verification failed: quality_score column was not added")
            
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    print("Starting database migration...")
    migrate_database()
    print("Migration completed.")
