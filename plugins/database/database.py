import datetime
import logging

import asyncpg

from plugins.config import Config

logger = logging.getLogger(__name__)


class Database:
    """Async PostgreSQL storage for user preferences and bot statistics.

    The connection pool is created lazily so importing plugins does not attempt
    a network connection. Neon connection strings are supplied via DATABASE_URL.
    """

    def __init__(self, uri, database_name=None):
        self.uri = uri
        self.database_name = database_name
        self._pool = None
        self._schema_ready = False
        self._schema_lock = None

    async def _ensure_pool(self):
        if self._pool is not None:
            return self._pool

        if not self.uri:
            raise RuntimeError(
                "DATABASE_URL is missing. Add your Neon PostgreSQL connection "
                "string to the hosting environment variables."
            )

        if self._schema_lock is None:
            import asyncio
            self._schema_lock = asyncio.Lock()

        async with self._schema_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    dsn=self.uri,
                    min_size=1,
                    max_size=5,
                    command_timeout=30,
                    server_settings={"application_name": "URL-uploader"},
                )
        return self._pool

    async def _ensure_schema(self):
        pool = await self._ensure_pool()
        if self._schema_ready:
            return pool

        if self._schema_lock is None:
            import asyncio
            self._schema_lock = asyncio.Lock()

        async with self._schema_lock:
            if not self._schema_ready:
                async with pool.acquire() as conn:
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id BIGINT PRIMARY KEY,
                            join_date DATE NOT NULL DEFAULT CURRENT_DATE,
                            apply_caption BOOLEAN NOT NULL DEFAULT TRUE,
                            upload_as_doc BOOLEAN NOT NULL DEFAULT FALSE,
                            thumbnail TEXT,
                            caption TEXT
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS channel_upload_history (
                            canonical_url TEXT PRIMARY KEY,
                            original_url TEXT NOT NULL,
                            title TEXT NOT NULL,
                            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS bot_settings (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        )
                    """)
                self._schema_ready = True
        return pool

    def new_user(self, id):
        return {
            "id": int(id),
            "join_date": datetime.date.today().isoformat(),
            "apply_caption": True,
            "upload_as_doc": False,
            "thumbnail": None,
            "caption": None,
        }

    async def add_user(self, id):
        pool = await self._ensure_schema()
        await pool.execute(
            """
            INSERT INTO users (id, join_date, apply_caption, upload_as_doc)
            VALUES ($1, CURRENT_DATE, TRUE, FALSE)
            ON CONFLICT (id) DO NOTHING
            """,
            int(id),
        )

    async def is_user_exist(self, id):
        pool = await self._ensure_schema()
        result = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1)",
            int(id),
        )
        return bool(result)

    async def total_users_count(self):
        pool = await self._ensure_schema()
        return await pool.fetchval("SELECT COUNT(*) FROM users")

    async def get_all_users(self):
        pool = await self._ensure_schema()
        rows = await pool.fetch("SELECT * FROM users ORDER BY id")
        return [dict(row) for row in rows]

    async def delete_user(self, user_id):
        pool = await self._ensure_schema()
        await pool.execute("DELETE FROM users WHERE id = $1", int(user_id))

    async def _set_preference(self, user_id, column, value):
        allowed_columns = {"apply_caption", "upload_as_doc", "thumbnail", "caption"}
        if column not in allowed_columns:
            raise ValueError("Unsupported user preference")
        pool = await self._ensure_schema()
        query = f"""
            INSERT INTO users (id, join_date, {column})
            VALUES ($1, CURRENT_DATE, $2)
            ON CONFLICT (id) DO UPDATE SET {column} = EXCLUDED.{column}
        """
        await pool.execute(query, int(user_id), value)

    async def _get_user_value(self, user_id, column, default):
        allowed_columns = {"apply_caption", "upload_as_doc", "thumbnail", "caption"}
        if column not in allowed_columns:
            raise ValueError("Unsupported user preference")
        pool = await self._ensure_schema()
        value = await pool.fetchval(
            f"SELECT {column} FROM users WHERE id = $1",
            int(user_id),
        )
        return default if value is None and column in {"apply_caption", "upload_as_doc"} else value if value is not None else default

    async def set_apply_caption(self, id, apply_caption):
        await self._set_preference(id, "apply_caption", bool(apply_caption))

    async def get_apply_caption(self, id):
        return await self._get_user_value(id, "apply_caption", True)

    async def set_upload_as_doc(self, id, upload_as_doc):
        await self._set_preference(id, "upload_as_doc", bool(upload_as_doc))

    async def get_upload_as_doc(self, id):
        return await self._get_user_value(id, "upload_as_doc", False)

    async def set_thumbnail(self, id, thumbnail):
        await self._set_preference(id, "thumbnail", thumbnail)

    async def get_thumbnail(self, id):
        return await self._get_user_value(id, "thumbnail", None)

    async def set_caption(self, id, caption):
        await self._set_preference(id, "caption", caption)

    async def get_caption(self, id):
        return await self._get_user_value(id, "caption", None)

    async def get_user_data(self, id) -> dict:
        pool = await self._ensure_schema()
        row = await pool.fetchrow("SELECT * FROM users WHERE id = $1", int(id))
        return dict(row) if row else None

    async def get_setting(self, key, default=None):
        pool = await self._ensure_schema()
        value = await pool.fetchval(
            "SELECT value FROM bot_settings WHERE key = $1",
            str(key),
        )
        return value if value is not None else default

    async def set_setting(self, key, value):
        pool = await self._ensure_schema()
        if value is None:
            await pool.execute("DELETE FROM bot_settings WHERE key = $1", str(key))
            return
        await pool.execute(
            """
            INSERT INTO bot_settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            str(key),
            str(value),
        )

    async def is_channel_video_uploaded(self, canonical_url):
        pool = await self._ensure_schema()
        return bool(await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM channel_upload_history WHERE canonical_url = $1)",
            str(canonical_url),
        ))

    async def record_channel_upload(self, canonical_url, original_url, title):
        pool = await self._ensure_schema()
        await pool.execute(
            """
            INSERT INTO channel_upload_history (canonical_url, original_url, title)
            VALUES ($1, $2, $3)
            ON CONFLICT (canonical_url) DO UPDATE
            SET original_url = EXCLUDED.original_url,
                title = EXCLUDED.title,
                uploaded_at = NOW()
            """,
            str(canonical_url),
            str(original_url),
            str(title or "Video"),
        )

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_ready = False


db = Database(Config.DATABASE_URL, "UploaderXNTBot")
