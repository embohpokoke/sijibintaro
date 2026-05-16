"""
SIJI Bintaro Database (PostgreSQL with SQLite-compatible interface)
Updated: 2026-03-07 - Migrated from SQLite to PostgreSQL
Schema: siji_bintaro
Based on: Livininbintaro CRM db.py pattern
"""

import os

import psycopg2
import psycopg2.pool
from datetime import datetime, date

# Configuration
DATABASE_URL = os.getenv(
    "SIJI_DB_URL",
    "postgresql://livin:L1v1n!B1nt4r0_2026@127.0.0.1:5432/livininbintaro",
)
DB_SCHEMA = "siji_bintaro"

# Connection pool
connection_pool = None


class DictRow(dict):
    """Dict that also supports integer indexing like SQLite Row"""
    def __getitem__(self, key):
        if isinstance(key, int):
            # Integer index - return nth value
            return list(self.values())[key]
        # String key - normal dict access
        return super().__getitem__(key)


class PostgreSQLiteCursor:
    """Cursor wrapper that returns SQLite-compatible rows"""
    
    def __init__(self, cursor):
        self._cursor = cursor
    
    def _make_dict_row(self, row):
        """Convert tuple row to DictRow using column names"""
        if row is None:
            return None
        if not self._cursor.description:
            return row
        # Get column names from cursor description
        columns = [desc[0] for desc in self._cursor.description]
        # Convert datetime/date to strings for SQLite compatibility
        converted = []
        for val in row:
            if isinstance(val, datetime):
                converted.append(val.strftime('%Y-%m-%d %H:%M:%S'))
            elif isinstance(val, date):
                converted.append(val.strftime('%Y-%m-%d'))
            else:
                converted.append(val)
        return DictRow(zip(columns, converted))
    
    def fetchone(self):
        row = self._cursor.fetchone()
        return self._make_dict_row(row)
    
    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._make_dict_row(row) for row in rows]
    
    def fetchmany(self, size=None):
        if size:
            rows = self._cursor.fetchmany(size)
        else:
            rows = self._cursor.fetchmany()
        return [self._make_dict_row(row) for row in rows]
    
    def execute(self, query, params=None):
        # Convert SQLite ? placeholders to PostgreSQL %s
        if '?' in query:
            query = query.replace('?', '%s')
        return self._cursor.execute(query, params)
    
    def executemany(self, query, params_list):
        if '?' in query:
            query = query.replace('?', '%s')
        return self._cursor.executemany(query, params_list)
    
    @property
    def rowcount(self):
        return self._cursor.rowcount
    
    @property
    def description(self):
        return self._cursor.description
    
    @property
    def lastrowid(self):
        # PostgreSQL doesn't have lastrowid, return None
        return None
    
    def close(self):
        return self._cursor.close()


class PostgreSQLiteConnection:
    """Connection wrapper that returns SQLite-compatible cursors"""
    
    def __init__(self, pg_conn):
        self._pg_conn = pg_conn
        # Set schema
        with self._pg_conn.cursor() as cur:
            cur.execute(f'SET search_path TO {DB_SCHEMA}, public')
        self._pg_conn.commit()
    
    
    def execute(self, sql, params=None):
        """Execute SQL directly on connection (SQLite compatibility)"""
        # Convert ? to %s for PostgreSQL
        sql = sql.replace("?", "%s")
        cursor = self._pg_conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        return PostgreSQLiteCursor(cursor)
    
    def cursor(self):
        pg_cursor = self._pg_conn.cursor()
        return PostgreSQLiteCursor(pg_cursor)
    
    def commit(self):
        return self._pg_conn.commit()
    
    def rollback(self):
        return self._pg_conn.rollback()
    
    def close(self):
        return self._pg_conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def init_connection_pool():
    """Initialize connection pool"""
    global connection_pool
    if connection_pool is None:
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            options=f'-c search_path={DB_SCHEMA},public'
        )


def get_db_connection():
    """Get database connection from pool (SQLite-compatible interface)"""
    global connection_pool
    if connection_pool is None:
        init_connection_pool()
    
    pg_conn = connection_pool.getconn()
    return PostgreSQLiteConnection(pg_conn)


def release_db_connection(conn):
    """Release connection back to pool"""
    global connection_pool
    if connection_pool and isinstance(conn, PostgreSQLiteConnection):
        connection_pool.putconn(conn._pg_conn)


# Context manager for automatic connection management
class get_db:
    """Context manager for database connections (SQLite-compatible)"""
    
    def __enter__(self):
        self.conn = get_db_connection()
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        release_db_connection(self.conn)


# For compatibility with existing code
def dict_factory(cursor, row):
    """Convert row to dictionary (SQLite-style)"""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class get_db_dict:
    """Context manager returning dictionaries (SQLite-compatible)"""
    
    def __enter__(self):
        self.conn = get_db_connection()
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        release_db_connection(self.conn)


# Initialize database (no-op for PostgreSQL, schema already exists)
def init_db():
    """Initialize database (no-op for PostgreSQL)"""
    # Schema already created during migration
    # This function exists for backward compatibility
    pass


# Initialize pool on module import
init_connection_pool()
