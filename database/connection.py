"""
MediVerse - Unified Database Connection Manager
Single DB pool used by ALL services (face recognition, chatbot, auth, etc.)
"""

import logging
from contextlib import contextmanager
from typing import Optional

import pyodbc

try:
    from sqlalchemy import create_engine, pool as sa_pool
except ImportError:
    create_engine = None
    sa_pool = None

from config.settings import settings

logger = logging.getLogger(__name__)


# =============================================================================
# SQLAlchemy-based pool (used by chatbot & new services)
# =============================================================================
class DatabaseManager:
    """
    Unified database manager using SQLAlchemy QueuePool for connection pooling.
    All services should use this instead of creating their own connections.
    """
    _engine = None

    @classmethod
    def get_engine(cls):
        if create_engine is None:
            raise RuntimeError(
                "SQLAlchemy is required. Install with: pip install sqlalchemy"
            )
        if cls._engine is None:
            cls._engine = create_engine(
                settings.db.get_sqlalchemy_url(),
                poolclass=sa_pool.QueuePool,
                pool_size=settings.db.POOL_SIZE,
                max_overflow=settings.db.MAX_OVERFLOW,
                pool_timeout=settings.db.TIMEOUT,
                pool_recycle=settings.db.POOL_RECYCLE,
                echo=False,
            )
            logger.info(
                f"Database engine created: pool_size={settings.db.POOL_SIZE}, "
                f"max_overflow={settings.db.MAX_OVERFLOW}"
            )
        return cls._engine

    @classmethod
    @contextmanager
    def get_connection(cls):
        """
        Context manager that yields a raw pyodbc connection.
        Auto-commits on success, rolls back on error, always closes.

        Usage:
            with DatabaseManager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT ...")
        """
        engine = cls.get_engine()
        conn = engine.raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @classmethod
    def dispose(cls):
        """Dispose of the engine and all connections."""
        if cls._engine is not None:
            cls._engine.dispose()
            cls._engine = None
            logger.info("Database engine disposed")

    @classmethod
    def health_check(cls) -> bool:
        """Quick health check: can we execute a simple query?"""
        try:
            with cls.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False


# =============================================================================
# PyODBC direct pool (used by face recognition service - backward compatible)
# =============================================================================
class PyODBCPool:
    """
    Simple pyodbc connection pool (backward-compatible with face recognition service).
    For new code, prefer DatabaseManager above.
    """

    def __init__(self, connection_string: str, pool_size: int = 10):
        self.connection_string = connection_string
        self.pool_size = pool_size
        self.connections: list = []
        self.in_use: set = set()
        self.created_count = 0
        logger.info(f"PyODBC pool initialized (size={pool_size})")

    def get_connection(self) -> pyodbc.Connection:
        # Try reuse
        for conn in self.connections:
            if conn not in self.in_use:
                try:
                    conn.timeout = settings.db.TIMEOUT
                    conn.execute("SELECT 1")
                    self.in_use.add(conn)
                    return conn
                except Exception:
                    self.connections.remove(conn)

        # Create new
        if len(self.connections) < self.pool_size:
            try:
                conn = pyodbc.connect(
                    self.connection_string, timeout=settings.db.TIMEOUT
                )
                self.connections.append(conn)
                self.in_use.add(conn)
                self.created_count += 1
                return conn
            except pyodbc.Error as e:
                logger.error(f"Failed to create connection: {e}")
                raise

        raise RuntimeError("No database connections available")

    def release_connection(self, conn: pyodbc.Connection):
        self.in_use.discard(conn)

    def close_all(self):
        for conn in self.connections:
            try:
                conn.close()
            except Exception:
                pass
        self.connections.clear()
        self.in_use.clear()


# Global pyodbc pool instance (initialized on startup)
_pyodbc_pool: Optional[PyODBCPool] = None


def init_pyodbc_pool():
    """Initialize the global pyodbc pool."""
    global _pyodbc_pool
    conn_str = settings.db.get_pyodbc_connection_string()
    _pyodbc_pool = PyODBCPool(conn_str, pool_size=settings.db.POOL_SIZE)
    logger.info("Global PyODBC pool initialized")


def get_pyodbc_pool() -> PyODBCPool:
    """Get the global pyodbc pool."""
    if _pyodbc_pool is None:
        raise RuntimeError("PyODBC pool not initialized. Call init_pyodbc_pool() first.")
    return _pyodbc_pool


@contextmanager
def get_db_connection():
    """
    Context manager for pyodbc connection (backward-compatible).
    Used by face_recognition_service.
    """
    p = get_pyodbc_pool()
    conn = p.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.release_connection(conn)
