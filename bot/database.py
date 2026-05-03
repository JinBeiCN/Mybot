import aiosqlite
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Tuple


class Database:
    def __init__(self, db_path: str, retention_days: int = 7):
        self.db_path = db_path
        self.retention_days = retention_days
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        """确保数据目录存在"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def _get_today_timestamp(self) -> int:
        """获取今日零点时间戳（使用本地时区）"""
        # 获取今日零点的时间戳
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(today.timestamp())

    async def init_db(self):
        """初始化数据库表"""
        async with aiosqlite.connect(self.db_path) as db:
            # 先检查并迁移旧数据（添加 date 字段和索引）
            await self._migrate_add_date_column(db)
            await self._migrate_add_extra_column(db)

            # 创建表（如果不存在）
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    date TEXT NOT NULL DEFAULT '',
                    extra TEXT NOT NULL DEFAULT ''
                )
            """)
            await db.commit()

    async def _migrate_add_extra_column(self, db):
        """迁移：为没有 extra 字段的记录添加扩展信息列"""
        cursor = await db.execute("PRAGMA table_info(chat_history)")
        columns = [row[1] for row in await cursor.fetchall()]

        if "extra" not in columns:
            await db.execute("ALTER TABLE chat_history ADD COLUMN extra TEXT NOT NULL DEFAULT ''")
            await db.commit()

    async def _migrate_add_date_column(self, db):
        """迁移：为没有 date 字段的记录添加日期和索引"""
        # 检查 date 列是否存在
        cursor = await db.execute("PRAGMA table_info(chat_history)")
        columns = [row[1] for row in await cursor.fetchall()]

        if "date" not in columns:
            # 添加 date 列
            await db.execute("ALTER TABLE chat_history ADD COLUMN date TEXT DEFAULT ''")
            await db.commit()

            # 填充已有的历史数据（使用 timestamp 转换）
            await db.execute("""
                UPDATE chat_history
                SET date = datetime(timestamp, 'unixepoch', 'localtime')
            """)
            # 只保留 YYYY-MM-DD 部分
            await db.execute("""
                UPDATE chat_history
                SET date = substr(date, 1, 10)
            """)
            await db.commit()
            print("数据库迁移完成：已添加 date 列并填充历史数据")

        # 确保索引存在
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_date_user
            ON chat_history(group_id, date, user_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_id
            ON chat_history(message_id)
        """)
        await db.commit()

    async def save_message(
        self,
        group_id: str,
        user_id: str,
        message: str,
        nickname: str = "",
        message_id: str = "",
        extra: dict | None = None,
    ):
        """保存消息到数据库"""
        # 检查是否有媒体信息需要保存
        has_media = extra and (extra.get("images"))
        message = message or ""

        # 过滤空消息（去除空白字符后为空且没有媒体的不保存）
        if not message.strip() and not has_media:
            return

        timestamp = int(datetime.now().timestamp())
        date = datetime.now().strftime("%Y-%m-%d")
        extra_json = json.dumps(extra or {}, ensure_ascii=False)

        # 如果消息为空但有媒体，用占位符
        if not message.strip() and has_media:
            message = "[图片]"

        async with aiosqlite.connect(self.db_path) as db:
            # 无论有没有 message_id 都保存消息，使用 timestamp 作为去重依据
            # 如果 message_id 存在，则用 message_id 去重
            if message_id:
                await db.execute(
                    "INSERT OR IGNORE INTO chat_history (message_id, group_id, user_id, nickname, message, timestamp, date, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (message_id, group_id, user_id, nickname, message, timestamp, date, extra_json)
                )
            else:
                # 没有 message_id 的消息也保存，但可能重复（通过 timestamp + user_id + group_id 判断）
                await db.execute(
                    "INSERT INTO chat_history (message_id, group_id, user_id, nickname, message, timestamp, date, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (message_id or "", group_id, user_id, nickname, message, timestamp, date, extra_json)
                )
            await db.commit()

    async def get_recent_messages(self, group_id: str, limit: int = 50) -> List[Tuple[str, str, int]]:
        """获取最近的消息"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, message, timestamp FROM chat_history WHERE group_id = ? ORDER BY timestamp DESC LIMIT ?",
                (group_id, limit)
            )
            rows = await cursor.fetchall()
            return list(reversed(rows))

    async def get_today_messages_with_extra(self, group_id: str, limit: int = 200) -> List[Tuple[str, str, int, str, str]]:
        """获取今日消息及扩展信息，返回 (user_id, message, timestamp, nickname, extra_json)"""
        today_date = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT user_id, message, timestamp, nickname, extra FROM chat_history WHERE group_id = ? AND date = ? ORDER BY timestamp ASC LIMIT ?",
                (group_id, today_date, limit)
            )
            return await cursor.fetchall()

    async def cleanup_old_messages(self):
        """清理过期消息（保留最近 retention_days 天）"""
        # 计算截止日期：删除 date < cutoff 的数据
        # 例如 retention_days=2，今天4月18日，只保留4月17和4月18日
        # cutoff = 4月18日 - (2-1)天 = 4月17日
        # 删除 4月17日之前的数据
        cutoff_date = (datetime.now() - timedelta(days=self.retention_days - 1)).strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM chat_history WHERE date < ?", (cutoff_date,))
            await db.commit()

    async def get_today_message_stats(self, group_id: str) -> List[Tuple[str, int, str]]:
        """获取今日各用户发言次数统计，返回 (user_id, count, nickname)

        使用 date 字段进行日期匹配，避免时区问题
        """
        today_date = datetime.now().strftime("%Y-%m-%d")

        async with aiosqlite.connect(self.db_path) as db:
            # 使用 date 字段精确匹配今天的日期
            cursor = await db.execute(
                """SELECT user_id, COUNT(*) as count, nickname
                   FROM chat_history
                   WHERE group_id = ? AND date = ?
                   GROUP BY user_id
                   ORDER BY count DESC""",
                (group_id, today_date)
            )
            rows = await cursor.fetchall()
            return rows

    async def get_today_message_stats_debug(self, group_id: str) -> dict:
        """调试用：获取今日统计的详细信息"""
        today_date = datetime.now().strftime("%Y-%m-%d")

        async with aiosqlite.connect(self.db_path) as db:
            # 获取今日总消息数
            cursor = await db.execute(
                "SELECT COUNT(*) FROM chat_history WHERE group_id = ? AND date = ?",
                (group_id, today_date)
            )
            total = (await cursor.fetchone())[0]

            # 获取日期范围内的消息
            cursor = await db.execute(
                "SELECT date, COUNT(*) FROM chat_history WHERE group_id = ? GROUP BY date ORDER BY date DESC LIMIT 5",
                (group_id,)
            )
            date_counts = await cursor.fetchall()

            # 获取今日各用户统计
            cursor = await db.execute(
                """SELECT user_id, COUNT(*) as count, nickname
                   FROM chat_history
                   WHERE group_id = ? AND date = ?
                   GROUP BY user_id
                   ORDER BY count DESC
                   LIMIT 10""",
                (group_id, today_date)
            )
            user_stats = await cursor.fetchall()

        return {
            "total_today": total,
            "date_counts": date_counts,
            "user_stats": user_stats,
            "query_date": today_date
        }

    async def get_message_by_id(self, message_id: str) -> Tuple[str, str, str, str]:
        """通过message_id获取消息信息，返回 (group_id, user_id, nickname, message)"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT group_id, user_id, nickname, message FROM chat_history WHERE message_id = ?",
                (message_id,)
            )
            row = await cursor.fetchone()
            return row if row else (None, None, None, None)

    async def get_message_context(self, message_id: str, group_id: str, total_chars: int = 500) -> dict:
        """获取消息的上下文

        Args:
            message_id: 被引用消息的ID
            group_id: 群号
            total_chars: 总字符数上限，默认500

        Returns:
            {
                "quoted": {...},  # 被引用的消息
                "before": str,    # 上文（可能被截断）
                "after": str      # 下文（可能被截断）
            }
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 获取被引用的消息
            cursor = await db.execute(
                "SELECT id, user_id, nickname, message, timestamp FROM chat_history WHERE message_id = ? AND group_id = ?",
                (message_id, group_id)
            )
            quoted_row = await cursor.fetchone()

            if not quoted_row:
                return {"quoted": None, "before": "", "after": ""}

            quoted_id = quoted_row[0]
            quoted = {
                "user_id": quoted_row[1],
                "nickname": quoted_row[2],
                "message": quoted_row[3],
                "timestamp": quoted_row[4]
            }

            # 获取上文（按时间排序，比当前消息早）
            cursor = await db.execute(
                """SELECT message FROM chat_history
                   WHERE group_id = ? AND id < ? AND date = (
                       SELECT date FROM chat_history WHERE id = ?
                   )
                   ORDER BY id DESC LIMIT 50""",
                (group_id, quoted_id, quoted_id)
            )
            before_messages = [row[0] for row in await cursor.fetchall()]
            before_messages.reverse()  # 按时间正序
            before_text = "\n".join(before_messages)

            # 获取下文（按时间排序，比当前消息晚）
            cursor = await db.execute(
                """SELECT message FROM chat_history
                   WHERE group_id = ? AND id > ? AND date = (
                       SELECT date FROM chat_history WHERE id = ?
                   )
                   ORDER BY id ASC LIMIT 50""",
                (group_id, quoted_id, quoted_id)
            )
            after_messages = [row[0] for row in await cursor.fetchall()]
            after_text = "\n".join(after_messages)

            # 计算上下文总长度
            quoted_len = len(quoted["message"])
            before_len = len(before_text)
            after_len = len(after_text)
            total_len = quoted_len + before_len + after_len

            # 如果超限，按规则分配
            if total_len > total_chars:
                # 先截断上文到平均长度
                avg_per_side = (total_chars - quoted_len) // 2
                before_text = before_text[:avg_per_side] + ("..." if before_len > avg_per_side else "")
                after_text = after_text[:avg_per_side] + ("..." if after_len > avg_per_side else "")

                # 重新计算，如果超限再处理
                current_len = len(before_text) + quoted_len + len(after_text)
                if current_len > total_chars:
                    # 下文为空时，剩余给上文
                    if not after_text.strip():
                        excess = current_len - total_chars
                        before_text = before_text[:-excess] + "..."
                    # 上文为空时，剩余给下文
                    elif not before_text.strip():
                        excess = current_len - total_chars
                        after_text = after_text[:-excess] + "..."

            return {
                "quoted": quoted,
                "before": before_text,
                "after": after_text
            }

    async def init_user_stats(self):
        """初始化用户统计表"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id TEXT PRIMARY KEY,
                    total_messages INTEGER DEFAULT 0,
                    first_seen INTEGER,
                    last_seen INTEGER,
                    style TEXT DEFAULT 'balanced'
                )
            """)
            await db.commit()

    async def update_user_stats(self, user_id: str):
        """更新用户统计"""
        import time
        timestamp = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO user_stats (user_id, total_messages, first_seen, last_seen)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    total_messages = total_messages + 1,
                    last_seen = ?
            """, (user_id, timestamp, timestamp, timestamp))
            await db.commit()

    async def get_user_stats(self, user_id: str) -> dict:
        """获取用户统计"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT total_messages, first_seen, last_seen, style FROM user_stats WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            if row:
                return {
                    "total_messages": row[0],
                    "first_seen": row[1],
                    "last_seen": row[2],
                    "style": row[3]
                }
            return None
