import asyncio
import json
import logging
import random
import re
import sys
import time
from email.utils import parsedate_to_datetime

import aiohttp
from datetime import datetime, timedelta
from pathlib import Path

from bot.websocket_client import WebSocketClient
from bot.message_handler import MessageHandler
from bot.command_router import CommandRouter
from bot.database import Database
from bot.context_manager import ContextManager
from bot.memory import UserMemory
from features.ai_features import AIFeatures
from features.tools import Tools
from features.fun_features import FunFeatures
from utils.siliconflow_api import SiliconFlowAPI
from utils.ranking_generator import RankingGenerator
from web.admin_server import AdminServer, MemoryLogHandler


# 自动触发关键词
AUTO_TRIGGER_KEYWORDS = ["今日逼话榜", "今日说话榜"]


class BeiXAIBot:
    def __init__(self, config_path: str = "config.json"):
        self.config = self.load_config(config_path)
        self.setup_logging()

        # 初始化组件
        self.db = Database(
            self.config["database"]["path"],
            self.config["database"]["retention_days"]
        )

        self.api = SiliconFlowAPI(
            self.config["siliconflow"]["api_keys"],
            self.config["siliconflow"]["base_url"],
            self.config["siliconflow"]["model"]
        )

        # 初始化上下文管理器
        context_rounds = self.config["bot"].get("context_rounds", 7)
        self.context_manager = ContextManager()

        # 获取幸运用户
        lucky_user = self.config["bot"].get("lucky_user", "")

        self.user_memory = UserMemory()
        self.ai_features = AIFeatures(self.api, self.db, self.context_manager, lucky_user)
        self.ai_features.cost_tracker = self._track_user_cost

        self.tools = Tools()
        self.fun_features = FunFeatures()
        self.command_router = CommandRouter(self.ai_features, self.tools, self.fun_features)
        bg_dir = self.config.get("backgrounds_dir", "data/backgrounds")
        self.ranking_generator = RankingGenerator(bg_dir=bg_dir)
        self.emotion_dir = self.config.get("emotion_dir", "data/emotions")

        # 关键词过滤配置
        self.filter_config = self.config.get("filter", {})
        self.filter_enabled = self.filter_config.get("enabled", False)
        self.filter_action = self.filter_config.get("action", "delete")
        self.filter_keywords = self.filter_config.get("keywords", [])

        # 用户画像配置
        self.user_profiles = self.config.get("user_profiles", {})

        # 用户分级配置
        self.tier_config = self.config.get("user_tiers", {
            "1": {"name": "基础用户", "context_rounds": 100, "daily_quota": 20},
            "2": {"name": "进阶用户", "context_rounds": 200, "daily_quota": 40},
            "3": {"name": "高级用户", "context_rounds": 500, "daily_quota": 9999999},
        })
        self.user_tiers: dict = {}  # {user_id: tier}
        self.daily_user_costs: dict = {}  # {user_id: {"cost": float, "date": str}}
        self.total_user_costs: dict = {}  # {user_id: float} 累计总消耗
        self.daily_ocr_counts: dict = {}  # {user_id: {"count": int, "date": str}}
        self.auto_profiles: dict = {}  # 自动生成的用户画像 {user_id: {traits, updated_at}}
        self._clear_memory_confirm: dict = {}  # 清空记忆确认状态 {user_id: {"count": int, "expires": float}}
        self._onboard_state: dict = {}  # 首次对话向导 {user_id: {"step": int, "answers": dict}}
        self.onboarded_users: set = set()  # 已完成向导的用户

        # 主动推送配置
        self.proactive_enabled = True
        self.proactive_interval_min = 1800  # 最短间隔 30 分钟
        self.proactive_interval_max = 7200  # 最长间隔 2 小时
        self.proactive_state: dict = {}  # {user_id: {"sent": int, "replied": bool, "last_sent": float}}
        self.proactive_next_at: float | None = None  # 下次推送时间戳
        self.proactive_target: str | None = None  # 下次推送目标

        # 硬检测开关
        self.hard_check_enabled = False
        self.hard_check_admin = "1992827821"  # 硬检测权限管理员

        # 速率限制配置
        self.rate_limit = 10  # 默认每分钟10次
        self.rate_window = 60  # 时间窗口60秒
        self.rate_records = {}  # {group_id: {"count": 次数, "window_start": 时间戳}}
        self.rate_admin = "1992827821"  # 速率限制管理员

        # 用户封禁列表
        self.blocked_users_path = "data/blocked_users.json"
        self.blocked_users: dict = {}

        # Web 管理后台配置
        web_config = self.config.get("web_admin", {})
        self.web_enabled = web_config.get("enabled", True)
        self.web_host = web_config.get("host", "127.0.0.1")
        self.web_port = web_config.get("port", 9101)
        self.web_token = web_config.get("token", "admin123")
        self.admin_server: AdminServer | None = None
        self.memory_log_handler: MemoryLogHandler | None = None

        # 定时打卡配置
        self.auto_sign_enabled = self.config.get("auto_sign", {}).get("enabled", False)
        self.auto_sign_groups = self.config.get("auto_sign", {}).get("groups", [])
        self.auto_sign_hour = self.config.get("auto_sign", {}).get("hour", 0)  # 0-23时
        self.auto_sign_minute = self.config.get("auto_sign", {}).get("minute", 5)  # 0-59分
        self.sign_last_date = self._load_sign_last_date()  # 从文件恢复上次打卡日期

        self.ws_client = WebSocketClient(
            self.config["napcat"]["host"],
            self.config["napcat"]["port"],
            self.config["napcat"]["token"],
            self.config["napcat"]["heartbeat_interval"]
        )

        # 事件日志文件
        self.event_log_path = "data/events.log"
        Path(self.event_log_path).parent.mkdir(parents=True, exist_ok=True)

        self.message_handler = None
        self.bot_qq = None

    def load_config(self, config_path: str) -> dict:
        """加载配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"配置文件 {config_path} 不存在")
            sys.exit(1)
        except json.JSONDecodeError:
            print(f"配置文件 {config_path} 格式错误")
            sys.exit(1)

    def setup_logging(self):
        """设置日志"""
        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        # 文件处理器
        file_handler = logging.FileHandler('bot.log', encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(fmt)
        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s [%(name)s] %(message)s', datefmt='%H:%M:%S'
        ))
        # 内存日志处理器（供 Web 查看）
        self.memory_log_handler = MemoryLogHandler(capacity=500)
        self.memory_log_handler.setLevel(logging.INFO)

        logging.basicConfig(
            level=logging.INFO,
            handlers=[file_handler, console_handler, self.memory_log_handler]
        )

        self.logger = logging.getLogger("BeiXAIBot")

        # 降低第三方库的日志级别
        logging.getLogger("websockets").setLevel(logging.WARNING)
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    def _load_sign_last_date(self) -> str | None:
        """从文件读取上次打卡日期"""
        try:
            sign_file = Path("data/sign_last_date.txt")
            if sign_file.exists():
                return sign_file.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass
        return None

    def _save_sign_last_date(self, date_str: str):
        """将打卡日期写入文件"""
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/sign_last_date.txt").write_text(date_str, encoding="utf-8")
        except Exception as e:
            self.logger.error(f"保存打卡日期失败: {e}")

    def _load_blocked_users(self):
        try:
            path = Path(self.blocked_users_path)
            if path.exists():
                self.blocked_users = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.blocked_users = {}

    def _save_blocked_users(self):
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path(self.blocked_users_path).write_text(
                json.dumps(self.blocked_users, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"保存封禁列表失败: {e}")

    def is_user_blocked(self, user_id: str) -> bool:
        return str(user_id) in self.blocked_users

    def block_user(self, user_id: str):
        self.blocked_users[str(user_id)] = time.time()
        self._save_blocked_users()
        self.logger.info(f"用户 {user_id} 已被封禁")

    def unblock_user(self, user_id: str) -> bool:
        if str(user_id) in self.blocked_users:
            del self.blocked_users[str(user_id)]
            self._save_blocked_users()
            self.logger.info(f"用户 {user_id} 已解除封禁")
            return True
        return False

    # ==================== 用户分级 ====================

    def _load_user_tiers(self):
        try:
            path = Path("data/user_tiers.json")
            if path.exists():
                self.user_tiers = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.user_tiers = {}

    def _save_user_tiers(self):
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/user_tiers.json").write_text(
                json.dumps(self.user_tiers, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"保存用户分级失败: {e}")

    def _load_daily_costs(self):
        try:
            path = Path("data/daily_costs.json")
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                today = datetime.now().strftime("%Y-%m-%d")
                # 只保留今天的记录
                self.daily_user_costs = {k: v for k, v in data.items() if v.get("date") == today}
        except Exception:
            self.daily_user_costs = {}

    def _save_daily_costs(self):
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/daily_costs.json").write_text(
                json.dumps(self.daily_user_costs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"保存配额记录失败: {e}")

    def _load_total_costs(self):
        try:
            path = Path("data/total_costs.json")
            if path.exists():
                self.total_user_costs = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.total_user_costs = {}

    def _save_total_costs(self):
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/total_costs.json").write_text(
                json.dumps(self.total_user_costs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"保存累计费用失败: {e}")

    def _load_auto_profiles(self):
        try:
            path = Path("data/auto_profiles.json")
            if path.exists():
                self.auto_profiles = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.auto_profiles = {}

    def _load_proactive_state(self):
        try:
            path = Path("data/proactive_state.json")
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.proactive_state = {k: v for k, v in raw.items()}
        except Exception:
            self.proactive_state = {}

    def _save_proactive_state(self):
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/proactive_state.json").write_text(
                json.dumps(self.proactive_state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"保存推送状态失败: {e}")

    def _save_auto_profiles(self):
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/auto_profiles.json").write_text(
                json.dumps(self.auto_profiles, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"保存自动画像失败: {e}")

    def get_user_tier(self, user_id: str) -> int:
        return int(self.user_tiers.get(str(user_id), 1))

    def set_user_tier(self, user_id: str, tier: int):
        self.user_tiers[str(user_id)] = tier
        self._save_user_tiers()
        self.logger.info(f"用户 {user_id} 分级设为 Lv.{tier}")

    def check_ocr_quota(self, user_id: str) -> tuple[bool, int, int]:
        """检查 OCR 配额 (1级20张/天, 2级50张/天, 3级不限)"""
        tier = self.get_user_tier(user_id)
        limits = {1: 20, 2: 50, 3: -1}
        limit = limits.get(tier, 20)
        if limit < 0:
            return True, 0, -1
        today = datetime.now().strftime("%Y-%m-%d")
        record = self.daily_ocr_counts.get(str(user_id), {"count": 0, "date": today})
        if record.get("date") != today:
            record = {"count": 0, "date": today}
        used = record.get("count", 0)
        return used < limit, used, limit

    def add_ocr_count(self, user_id: str):
        today = datetime.now().strftime("%Y-%m-%d")
        key = str(user_id)
        record = self.daily_ocr_counts.get(key, {"count": 0, "date": today})
        if record["date"] != today:
            record = {"count": 0, "date": today}
        record["count"] += 1
        self.daily_ocr_counts[key] = record

    def get_tier_quota(self, tier: int) -> float:
        """获取某级的每日额度，-1 表示无限"""
        return float(self.tier_config.get(str(tier), {}).get("daily_quota", 20))

    def check_daily_quota(self, user_id: str) -> tuple[bool, float, float]:
        """检查用户额度，返回 (是否通过, 已用, 限额)"""
        today = datetime.now().strftime("%Y-%m-%d")
        record = self.daily_user_costs.get(str(user_id), {})
        # 自定义配额优先
        custom = record.get("custom_quota")
        if custom is not None:
            quota = float(custom)
        else:
            tier = self.get_user_tier(user_id)
            quota = self.get_tier_quota(tier)
        if quota < 0:
            return True, 0, -1

        if record.get("date") != today:
            return True, 0, quota

        used = record.get("cost", 0)
        return used < quota, used, quota

    def _track_user_cost(self, user_id: str, cost: float):
        """配额追踪回调（由 ai_features 调用）"""
        today = datetime.now().strftime("%Y-%m-%d")
        key = str(user_id)
        # 每日
        record = self.daily_user_costs.get(key, {"cost": 0, "date": today})
        if record["date"] != today:
            record = {"cost": 0, "date": today}
        record["cost"] += cost
        self.daily_user_costs[key] = record
        # 累计
        total = self.total_user_costs.get(key, 0)
        self.total_user_costs[key] = total + cost
        self._save_daily_costs()
        self._save_total_costs()

    # ==================== 自动用户画像 ====================

    async def _extract_memories(self, user_id: str, user_msg: str, ai_reply: str):
        """从对话中提取长期记忆（后台异步执行，不影响回复速度）"""
        try:
            # 每 8 次交互提取一次，避免频繁调用 API
            stats = self.user_memory.get_stats(user_id)
            if stats["total_interactions"] % 8 != 0:
                return

            context = self.context_manager.get_context(f"private_{user_id}", user_id)
            recent = "\n".join(
                f"{'用户' if m['role'] == 'user' else 'Hina'}: {m['content'][:300]}"
                for m in context[-10:]
            )

            prompt = """从以下对话中提取关于"用户"的关键信息。只提取用户的信息，不要提取Hina的信息。
返回 JSON 数组，每条包含 type 和 content：
- preference: 用户明确或暗示喜欢的事物
- dislike: 用户明确或暗示讨厌的事物
- personality: 用户性格特点
- habit: 用户习惯
- event: 用户提到的生活事件
- skill: 用户擅长的事情
- goal: 用户的目标或计划
- fact: 其他值得记住的信息

只提取有把握的（>70%确定），不要猜测。如果没有值得提取的，返回空数组 []。
只返回 JSON 数组，不要其他文字。"""

            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"对话记录：\n{recent}"},
            ]
            result = await self.api.chat_completion(messages, use_claude_persona=False, max_tokens=500, temperature=0.3)
            if result:
                try:
                    match = re.search(r"\[.*\]", result, re.DOTALL)
                    if match:
                        facts = json.loads(match.group())
                        for f in facts:
                            if isinstance(f, dict) and "type" in f and "content" in f:
                                self.user_memory.add_fact(
                                    user_id, f["type"], f["content"],
                                    confidence=0.7
                                )
                        if facts:
                            self.logger.info(f"提取用户 {user_id} 记忆: {len(facts)} 条")
                except Exception:
                    pass

            # 每 50 次交互生成一次记忆摘要
            if stats["total_interactions"] > 0 and stats["total_interactions"] % 50 == 0:
                await self._generate_memory_summary(user_id)

            # 每 20 次交互提取一次风格指纹
            if stats["total_interactions"] > 0 and stats["total_interactions"] % 20 == 0:
                context = self.context_manager.get_context(f"private_{user_id}", user_id)
                user_msgs = [m["content"] for m in context[-40:] if m["role"] == "user"]
                if len(user_msgs) >= 10:
                    fp = self.user_memory.extract_style_fingerprint(user_msgs)
                    desc = self.user_memory.set_style(user_id, fp)
                    self.logger.info(f"更新用户 {user_id} 风格指纹: {desc[:50]}...")
        except Exception as e:
            self.logger.error(f"提取记忆失败: {e}")

    async def _generate_memory_summary(self, user_id: str):
        """生成用户记忆摘要"""
        try:
            data = self.user_memory.load(user_id)
            facts = data.get("facts", [])
            if len(facts) < 5:
                return

            fact_text = "\n".join(f"- [{f['type']}] {f['content']}" for f in facts[-50:])
            prompt = "根据以下关于一个用户的零散记忆，用 2-3 句话总结这个人的性格、喜好和特点。要自然、不刻板，像朋友描述朋友。"
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": fact_text},
            ]
            result = await self.api.chat_completion(messages, use_claude_persona=False, max_tokens=150, temperature=0.5)
            if result:
                self.user_memory.set_summary(user_id, result.strip())
                self.logger.info(f"已生成用户 {user_id} 记忆摘要")
        except Exception as e:
            self.logger.error(f"生成记忆摘要失败: {e}")

    async def _update_auto_profile(self, user_id: str, recent_messages: str):
        """根据最近的私聊内容自动更新用户画像"""
        try:
            # 每 15 条消息更新一次
            profile = self.auto_profiles.get(str(user_id), {})
            msg_count = profile.get("msg_count", 0) + 1
            if msg_count % 15 != 0:
                profile["msg_count"] = msg_count
                self.auto_profiles[str(user_id)] = profile
                return

            system_prompt = """从以下对话中提取用户的特征，用 JSON 格式返回：
{"name": "称呼(不明确则用"朋友")", "traits": ["特点1", "特点2", "特点3"], "interests": ["兴趣1", "兴趣2"]}
只返回 JSON，不超过 100 字。"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": recent_messages[-2000:]},
            ]
            result = await self.api.chat_completion(messages, use_claude_persona=False, max_tokens=200)
            if result:
                try:
                    # 尝试从返回中提取 JSON
                    import re as _re
                    match = _re.search(r"\{[^}]+\}", result)
                    if match:
                        parsed = json.loads(match.group())
                        profile.update(parsed)
                        profile["msg_count"] = msg_count
                        profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        self.auto_profiles[str(user_id)] = profile
                        self._save_auto_profiles()
                        self.logger.info(f"已更新用户 {user_id} 的自动画像")
                except Exception:
                    pass
        except Exception as e:
            self.logger.error(f"更新自动画像失败: {e}")

    def get_auto_profile(self, user_id: str) -> dict | None:
        return self.auto_profiles.get(str(user_id))

    def _get_emotion_image(self, emotion: str) -> str | None:
        """从情绪文件夹里随机取一张图片，返回文件路径"""
        import glob as _glob
        folder = Path(self.emotion_dir) / emotion.strip().lower()
        if not folder.is_dir():
            return None
        files = _glob.glob(str(folder / "*"))
        images = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'))]
        return random.choice(images) if images else None

    @staticmethod
    def _split_long_text(text: str, max_len: int = 500) -> list[str]:
        """将长文本按句子边界拆分为多段"""
        if len(text) <= max_len:
            return [text]
        parts = []
        remaining = text
        while len(remaining) > max_len:
            cut = remaining.rfind("。", 0, max_len)
            if cut == -1:
                cut = remaining.rfind("！", 0, max_len)
            if cut == -1:
                cut = remaining.rfind("？", 0, max_len)
            if cut == -1:
                cut = remaining.rfind("~", 0, max_len)
            if cut == -1:
                cut = max_len
            else:
                cut += 1  # 保留标点
            parts.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            parts.append(remaining)
        return parts

    def log_event(self, event_type: str, message: str):
        """记录事件到日志文件 + logger"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{event_type}] {message}\n"
        with open(self.event_log_path, "a", encoding="utf-8") as f:
            f.write(log_line)
        self.logger.info(f"[EVENT:{event_type}] {message}")

    def is_auto_trigger_command(self, text: str) -> bool:
        """检查是否为自动触发的命令"""
        text_lower = text.lower()
        for keyword in AUTO_TRIGGER_KEYWORDS:
            if keyword in text_lower or keyword in text:
                return True
        return False

    def check_keyword_filter(self, text: str) -> bool:
        """检查消息是否包含敏感关键词"""
        if not self.filter_enabled or not self.filter_keywords:
            return False

        text_lower = text.lower()
        for keyword in self.filter_keywords:
            if keyword.lower() in text_lower:
                return True
        return False

    def check_rate_limit(self, group_id: str) -> bool:
        """检查速率限制，返回True表示通过，False表示超限"""
        current_time = time.time()

        if group_id not in self.rate_records:
            self.rate_records[group_id] = {"count": 1, "window_start": current_time}
            return True

        record = self.rate_records[group_id]

        # 检查是否在时间窗口内
        if current_time - record["window_start"] >= self.rate_window:
            # 重置窗口
            record["count"] = 1
            record["window_start"] = current_time
            return True

        # 检查次数是否超限
        if record["count"] >= self.rate_limit:
            return False

        record["count"] += 1

        # 每 200 次调用触发一次过期记录清理
        self._rate_check_count = getattr(self, '_rate_check_count', 0) + 1
        if self._rate_check_count % 200 == 0:
            self._prune_rate_records()

        return True

    def _prune_rate_records(self):
        """清理超过 2 个窗口期的僵尸群记录"""
        now = time.time()
        stale = [gid for gid, r in self.rate_records.items()
                  if now - r["window_start"] > self.rate_window * 2]
        for gid in stale:
            del self.rate_records[gid]

    async def handle_message(self, raw_message: str):
        """处理接收到的消息"""
        try:
            message = self.message_handler.parse_message(raw_message)
            if not message:
                return

            # 获取bot自己的QQ号（从登录信息中获取）
            if message.get("post_type") == "meta_event" and message.get("meta_event_type") == "lifecycle":
                # 可以在这里获取bot的QQ号
                pass

            # 处理退群事件
            leave_info = self.message_handler.get_leave_info(message)
            if leave_info:
                await self.handle_group_decrease(leave_info)
                return

            # 处理撤回事件
            recall_info = self.message_handler.get_recall_info(message)
            if recall_info:
                await self.handle_group_recall(recall_info)
                return

            # === 私聊消息处理 ===
            if self.message_handler.is_private_message(message):
                await self._handle_private_message(message)
                return

            # 只处理群消息
            if not self.message_handler.is_group_message(message):
                return

            group_id = self.message_handler.get_group_id(message)
            user_id = self.message_handler.get_user_id(message)
            plain_text = self.message_handler.get_plain_text(message)
            nickname = self.message_handler.get_nickname(message)
            message_id = str(message.get("message_id", ""))

            # 忽略 bot 自己发送的消息（避免反射触发）
            if user_id == self.bot_qq:
                return

            # 检查是否@了机器人
            is_mentioned = self.message_handler.is_mentioned(message)
            is_auto_trigger = self.is_auto_trigger_command(plain_text)

            # 自动触发命令，不保存消息
            if is_auto_trigger:
                self.logger.info(f"自动触发排行榜 (群:{group_id})")
                await self.generate_and_send_ranking(group_id)
                return

            # @命令，不保存消息
            if is_mentioned:
                # 提取命令
                command, args = self.message_handler.extract_command(message)

                # 获取用户画像（提前获取，供后续使用）
                user_profile = self.user_profiles.get(user_id, {})

                if not command:
                    return

                self.logger.info(f"处理命令: {command} (群:{group_id}, 用户:{user_id})")

                # 评论命令特殊处理：获取引用消息的ID
                if command == "评论" or command == "锐评":
                    reply_id = self.message_handler.get_reply_message_id(message)
                    if not reply_id:
                        await self.ws_client.send_group_message(group_id, "请引用要评论的消息后使用此命令")
                        return
                    if not self.check_rate_limit(group_id):
                        return
                    response = await self.ai_features.comment_chat(group_id, reply_id)
                    await self.ws_client.send_group_message(group_id, response)
                    return

                # 硬检测开关命令（仅管理员可用）
                if command in ["1", "0"]:
                    if user_id == self.hard_check_admin:
                        if command == "1":
                            self.hard_check_enabled = True
                            await self.ws_client.send_group_message(group_id, "当前安全检测：严格模式 ✓")
                        else:
                            self.hard_check_enabled = False
                            await self.ws_client.send_group_message(group_id, "当前安全检测：宽松模式")
                    else:
                        mode = "严格模式" if self.hard_check_enabled else "宽松模式"
                        await self.ws_client.send_group_message(group_id, f"安全检测模式：{mode}")
                    return

                # 速率限制命令
                if command == "速率":
                    if args:
                        # 设置速率
                        if user_id == self.rate_admin:
                            try:
                                new_limit = int(args)
                                if new_limit < 1:
                                    await self.ws_client.send_group_message(group_id, "速率值必须大于0")
                                else:
                                    self.rate_limit = new_limit
                                    await self.ws_client.send_group_message(group_id, f"本群AI速率已设置为：{new_limit}次/分钟")
                            except ValueError:
                                await self.ws_client.send_group_message(group_id, "请输入有效的数字")
                        else:
                            await self.ws_client.send_group_message(group_id, f"当前速率：{self.rate_limit}次/分钟")
                    else:
                        # 查看速率
                        await self.ws_client.send_group_message(group_id, f"当前速率：{self.rate_limit}次/分钟")
                    return

                # ==================== 后门命令（管理员专用） ====================
                fortune_admin = self.config["bot"].get("admin_qq", [])
                if not fortune_admin:
                    fortune_admin = []
                is_admin = user_id in fortune_admin or user_id == self.config["bot"].get("lucky_user", "")

                # !setfortune @用户 等级 - 设置自定义运势等级
                if command == "!setfortune" and is_admin:
                    # 获取被@的用户列表
                    mentioned_users = self.message_handler.get_mentioned_users(message)
                    # 排除机器人自己的QQ
                    mentioned_users = [u for u in mentioned_users if u != self.bot_qq]

                    if args and len(mentioned_users) > 0:
                        target_qq = mentioned_users[0]
                        parts = args.split()
                        if len(parts) >= 2:
                            try:
                                stars = int(parts[1])
                                result = self.ai_features.set_fortune(target_qq, stars)
                                await self.ws_client.send_group_message(group_id, result)
                            except ValueError:
                                await self.ws_client.send_group_message(group_id, "参数格式错误，正确格式：!setfortune @用户 等级（1-10）")
                        else:
                            await self.ws_client.send_group_message(group_id, "参数不足，正确格式：!setfortune @用户 等级（1-10）")
                    else:
                        await self.ws_client.send_group_message(group_id, "请 @ 要设置运势的用户，正确格式：!setfortune @用户 等级（1-10）")
                    return

                # !reroll @用户 - 重新抽取运势
                if command == "!reroll" and is_admin:
                    # 获取被@的用户列表
                    mentioned_users = self.message_handler.get_mentioned_users(message)
                    # 排除机器人自己的QQ
                    mentioned_users = [u for u in mentioned_users if u != self.bot_qq]

                    if len(mentioned_users) > 0:
                        target_qq = mentioned_users[0]
                        result = self.ai_features.reroll_fortune(target_qq)
                        await self.ws_client.send_group_message(group_id, result)
                    else:
                        await self.ws_client.send_group_message(group_id, "请 @ 要重新抽取运势的用户，正确格式：!reroll @用户")
                    return

                # 更新用户统计
                await self.db.update_user_stats(user_id)

                # 获取用户分级
                user_tier = self.get_user_tier(user_id)

                # AI 命令需要检查配额
                cmd_is_local = command in ("清除上下文", "黄历", "笑话", "星座", "一言", "帮助", "help", "菜单", "信息", "运势", "个人运势")
                if not cmd_is_local:
                    ok, used, limit = self.check_daily_quota(user_id)
                    if not ok:
                        await self.ws_client.send_group_message(
                            group_id, f"今日 AI 额度已用尽 (${used:.2f}/${limit:.0f})，明天零点自动重置"
                        )
                        return

                # 调用命令路由（传入 tier 供 AI 上下文使用）
                is_local, response = await self.command_router.route_command(
                    command, args, group_id, user_id, user_profile, tier=user_tier
                )

                # 特殊处理：生成总结图片
                if response == "__GENERATE_SUMMARY_IMAGE__":
                    if not self.check_rate_limit(group_id):
                        return
                    success, result = await self.ai_features.summarize_today_image(group_id)
                    if success:
                        await self.ws_client.send_group_image(group_id, result)
                    else:
                        await self.ws_client.send_group_message(group_id, result)
                    return

                # 特殊处理：运势图片
                if response == "__FORTUNE_IMAGE__":
                    if not self.check_rate_limit(group_id):
                        return
                    fortune_result = await self.ai_features.generate_fortune(user_id)
                    if isinstance(fortune_result, tuple) and fortune_result[0] == "__FORTUNE_IMAGE__":
                        await self.ws_client.send_group_image(group_id, fortune_result[1])
                    else:
                        await self.ws_client.send_group_message(group_id, str(fortune_result))
                    return

                # AI命令需要检查速率限制
                if not is_local and not self.check_rate_limit(group_id):
                    return

                # 发送响应
                await self.ws_client.send_group_message(group_id, response)
                return

            # 普通消息，只保存到数据库，不回复AI
            # 只要有 message_id 就保存（表情/图片等也计入）
            if group_id and user_id and message_id:
                # 宽松命令检测：检查是否是运势命令
                loose_cmd, loose_args = self.message_handler.detect_loose_command(message)
                if loose_cmd in ("运势", "个人运势"):
                    # 是宽松运势命令，走命令处理流程
                    self.logger.info(f"宽松触发运势: {loose_cmd} (群:{group_id}, 用户:{user_id})")
                    # 更新用户统计
                    await self.db.update_user_stats(user_id)
                    # 获取用户画像
                    user_profile = self.user_profiles.get(user_id, {})
                    # 检查速率限制
                    if not self.check_rate_limit(group_id):
                        return
                    # 生成运势图片
                    response = await self.ai_features.generate_fortune(user_id)
                    if isinstance(response, tuple) and response[0] == "__FORTUNE_IMAGE__":
                        await self.ws_client.send_group_image(group_id, response[1])
                    else:
                        await self.ws_client.send_group_message(group_id, str(response))
                    return

                # 关键词过滤检测
                if self.check_keyword_filter(plain_text):
                    self.logger.info(f"检测到敏感词，已处理 (群:{group_id}, 用户:{user_id})")
                    return

                # 即使 plain_text 为空（有表情/图片等），也保存
                media_info = self.message_handler.extract_media_info(message)
                await self.db.save_message(group_id, user_id, plain_text, nickname, message_id, extra=media_info)

        except Exception as e:
            self.logger.error(f"处理消息失败: {e}", exc_info=True)

    async def _handle_private_message(self, message: dict):
        """处理私聊消息（专属贴心朋友模式 + 分级配额）"""
        try:
            user_id = self.message_handler.get_user_id(message)
            plain_text = self.message_handler.get_plain_text(message)

            if user_id == self.bot_qq:
                return

            if self.is_user_blocked(user_id):
                await self.ws_client.send_private_message(user_id, "你的服务已被禁用")
                return

            # 用户回复即重置主动推送计数
            if user_id in self.proactive_state:
                self.proactive_state[user_id]["replied"] = True
                self.proactive_state[user_id]["sent"] = 0
                self._save_proactive_state()

            # === 图片 OCR 处理 ===
            media = self.message_handler.extract_media_info(message)
            image_urls = []
            for img in media.get("images", []):
                url = img.get("url", "")
                if url:
                    image_urls.append(url)
            # 清理过期的 OCR 图片 URL（去掉已有 CQ 码中的图片）
            if plain_text:
                plain_text = re.sub(r"\[CQ:image[^\]]*\]", "", plain_text).strip()

            if image_urls:
                ocr_results = []
                for url in image_urls[:3]:  # 最多3张
                    ok, used, limit = self.check_ocr_quota(user_id)
                    if not ok:
                        await self.ws_client.send_private_message(
                            user_id, f"今日 OCR 次数已用完 ({used}/{limit})，明天重置"
                        )
                        break
                    ocr_text = await self.api.ocr_image_url(url)
                    self.add_ocr_count(user_id)
                    if ocr_text:
                        ocr_results.append(f"[图片内容: {ocr_text}]")
                if ocr_results:
                    plain_text = "\n".join(ocr_results) + ("\n" + plain_text if plain_text else "")

            # === 首次对话向导 ===
            command, args = self.message_handler.extract_command(message)

            # "设置偏好" 可以重新触发向导
            if command == "设置偏好":
                self._onboard_state[user_id] = {"step": 1, "answers": {}}
                self.onboarded_users.discard(user_id)

            # 向导进行中
            if user_id in self._onboard_state and user_id not in self.onboarded_users:
                state = self._onboard_state[user_id]
                step = state["step"]
                text = plain_text.strip()

                if text == "跳过" or command == "跳过":
                    del self._onboard_state[user_id]
                    self.onboarded_users.add(user_id)
                    await self.ws_client.send_private_message(user_id, "好的，跳过设置。随时可以发「设置偏好」重新配置~")
                    return

                if step == 1:
                    state["answers"]["name"] = text
                    state["step"] = 2
                    await self.ws_client.send_private_message(user_id, "好的！那平时有什么兴趣爱好吗？比如游戏、动漫、音乐、运动之类的？")
                    return
                elif step == 2:
                    state["answers"]["interests"] = text
                    state["step"] = 3
                    await self.ws_client.send_private_message(user_id, "了解！你希望我怎么跟你聊天呢？\n1. 话多热情\n2. 简洁冷淡\n3. 毒舌吐槽\n4. 随意自然\n\n回复数字或描述都行~")
                    return
                elif step == 3:
                    state["answers"]["style"] = text
                    del self._onboard_state[user_id]
                    self.onboarded_users.add(user_id)
                    # 保存到 auto_profiles
                    profile = self.auto_profiles.get(str(user_id), {})
                    profile["name"] = state["answers"].get("name", "")
                    profile["interests"] = state["answers"].get("interests", "")
                    profile["style"] = state["answers"].get("style", "")
                    profile["onboarded"] = True
                    self.auto_profiles[str(user_id)] = profile
                    self._save_auto_profiles()
                    await self.ws_client.send_private_message(
                        user_id,
                        f"收到！都记住啦~ {state['answers'].get('name', '')}，以后就这么聊。来，想说点什么？"
                    )
                    return

            # 新用户首次对话，触发向导
            if not command and user_id not in self.onboarded_users and user_id not in self._onboard_state:
                self._onboard_state[user_id] = {"step": 1, "answers": {}}
                await self.ws_client.send_private_message(
                    user_id,
                    "嗨！初次见面~ 在开始之前，想简单了解一下你的偏好，方便我更好地陪你聊天。\n\n首先，怎么称呼你？（回复你的名字或昵称，发「跳过」则使用默认设置）"
                )
                return

            # 本地命令（不需要配额检查）
            if command == "帮助" or command == "help" or command == "菜单":
                await self.ws_client.send_private_message(user_id, self.fun_features.get_help())
                return
            if command == "信息":
                await self.ws_client.send_private_message(user_id, "Hina Bot\n版本: 1.0.0\n开发者: JinBei")
                return
            if command == "黄历":
                await self.ws_client.send_private_message(user_id, self.tools.get_huangli())
                return
            if command == "笑话":
                await self.ws_client.send_private_message(user_id, self.tools.get_joke())
                return
            if command == "一言":
                await self.ws_client.send_private_message(user_id, self.fun_features.get_daily_quote())
                return
            if command == "清空记忆":
                now = time.time()
                state = self._clear_memory_confirm.get(user_id)
                # 清理过期确认（超过 5 分钟）
                if state and now - state.get("expires", 0) > 300:
                    state = None

                if not state or state["count"] == 1:
                    self._clear_memory_confirm[user_id] = {"count": 2, "expires": now + 300}
                    await self.ws_client.send_private_message(
                        user_id, "确定要清空我关于你的所有记忆吗？这将删除对话历史和个人画像。再发一次「清空记忆」继续。"
                    )
                    return
                elif state["count"] == 2:
                    self._clear_memory_confirm[user_id] = {"count": 3, "expires": now + 300}
                    await self.ws_client.send_private_message(
                        user_id, "最后一次确认：真的真的要删除所有记忆吗？再发一次「清空记忆」执行。"
                    )
                    return
                else:
                    # 第三次确认，执行清空
                    del self._clear_memory_confirm[user_id]
                    pvt_gid = f"private_{user_id}"
                    self.ai_features.clear_context(pvt_gid, user_id)
                    self.auto_profiles.pop(str(user_id), None)
                    self._save_auto_profiles()
                    self.logger.info(f"已清空用户 {user_id} 的记忆")
                    await self.ws_client.send_private_message(user_id, "好了，我什么都不记得了。")
                    return

            if command == "我的余额" or command == "余额":
                ok, used, quota = self.check_daily_quota(user_id)
                remaining = max(0, quota - used) if quota > 0 else 0
                total = self.total_user_costs.get(str(user_id), 0)
                quota_str = f"${quota:,.0f}" if quota > 0 else "无限"
                await self.ws_client.send_private_message(
                    user_id,
                    f"今日已用: ${used:.4f} / {quota_str}\n今日剩余: ${remaining:,.2f}\n累计消耗: ${total:,.4f}"
                )
                return

            if command == "清除上下文":
                self.ai_features.clear_context(f"private_{user_id}", user_id)
                await self.ws_client.send_private_message(user_id, "已清除对话上下文")
                return

            if command:
                self.logger.info(f"私聊命令: {command} (用户:{user_id})")

            # 速率检查
            if not self.check_rate_limit(f"private_{user_id}"):
                await self.ws_client.send_private_message(user_id, "请求太频繁，请稍后再试")
                return

            # 配额检查
            ok, used, limit = self.check_daily_quota(user_id)
            if not ok:
                await self.ws_client.send_private_message(
                    user_id, f"今日 AI 额度已用尽 (${used:.2f}/${limit:.0f})，明天零点自动重置"
                )
                return

            # 获取分级和画像
            tier = self.get_user_tier(user_id)
            manual_profile = self.user_profiles.get(user_id, {})
            auto_profile = self.get_auto_profile(user_id) or {}
            user_profile = {**auto_profile, **manual_profile}  # 手动配置优先

            response = None

            # 本地命令（不需要 AI）
            if command == "运势" or command == "个人运势":
                response = await self.ai_features.generate_fortune(user_id)
                if isinstance(response, tuple) and response[0] == "__FORTUNE_IMAGE__":
                    await self.ws_client.send_private_image(user_id, response[1])
                return
            elif command == "星座":
                if not args:
                    await self.ws_client.send_private_message(user_id, "请提供星座名称，例如：星座 白羊座")
                    return
                response = self.fun_features.get_zodiac_fortune(args)
            elif command == "作诗":
                response = await self.ai_features.generate_poem(args)
            elif command == "接龙":
                if not args:
                    await self.ws_client.send_private_message(user_id, "请提供一个成语，例如：接龙 一马当先")
                    return
                response = await self.ai_features.idiom_solitaire(args)
            else:
                # 默认走 AI 贴心朋友对话（附带时间上下文 + 长期记忆）
                full_msg = f"{command} {args}" if command and args else (command or plain_text)
                now = datetime.now()
                time_ctx = f"[当前时间: {now.strftime('%Y年%m月%d日 %H:%M')}，{['凌晨','早上','上午','中午','下午','傍晚','晚上','深夜'][min(7, now.hour // 3)]}]"
                # 注入长期记忆 + 语气风格
                memory_ctx = self.user_memory.build_memory_prompt(user_id, plain_text or (command or ""))
                style_ctx = self.user_memory.build_style_prompt(user_id)
                full_msg = f"{time_ctx} {full_msg}"
                if memory_ctx:
                    full_msg = memory_ctx + "\n" + full_msg
                if style_ctx:
                    full_msg = style_ctx + "\n" + full_msg
                response = await self.ai_features.chat_with_profile(
                    f"private_{user_id}", user_id, full_msg,
                    user_profile=user_profile, tier=tier, private_chat=True,
                )
                # 异步提取新记忆 + 更新画像
                asyncio.create_task(self._extract_memories(user_id, plain_text, response))
                self.user_memory.increment_interactions(user_id)

            if response:
                # TODO: 表情包功能 — 等 data/emotions/ 目录里放好图片后启用
                # 启用时取消下面这行注释，替换整个 if response 块
                response = re.sub(r"\[emotion:[a-zA-Z_]+\]", "", response)
                for chunk in self._split_long_text(response):
                    await self.ws_client.send_private_message(user_id, chunk)
                    await asyncio.sleep(0.3)

        except Exception as e:
            self.logger.error(f"处理私聊消息失败: {e}", exc_info=True)

    async def handle_group_decrease(self, leave_info: dict):
        """处理群成员退出事件"""
        try:
            group_id = leave_info["group_id"]
            user_id = leave_info["user_id"]
            sub_type = leave_info["sub_type"]

            self.logger.info(f"群成员退出 (群:{group_id}, 用户:{user_id}, 类型:{sub_type})")

            # 获取成员昵称（如果有）
            nickname = user_id  # 默认用QQ号

            if sub_type == "kick_me":
                msg = f"Hina Bot被踢出群 {group_id}"
            elif sub_type == "kick":
                msg = f"用户 {nickname}({user_id}) 被踢出群 {group_id}"
            else:
                msg = f"用户 {nickname}({user_id}) 主动退群 {group_id}"

            # 写入日志文件
            self.log_event("LEAVE", msg)

        except Exception as e:
            self.logger.error(f"处理退群事件失败: {e}")

    async def handle_group_recall(self, recall_info: dict):
        """处理群消息撤回事件"""
        try:
            group_id = recall_info["group_id"]
            user_id = recall_info["user_id"]
            message_id = recall_info["message_id"]

            self.logger.info(f"群消息撤回 (群:{group_id}, 用户:{user_id}, 消息ID:{message_id})")

            # 从数据库查询被撤回的消息
            _, recaller_id, nickname, content = await self.db.get_message_by_id(message_id)

            # 如果数据库查不到被撤回的消息，不处理
            if not content:
                return

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 检查是否为链接/图片/视频
            is_media = any([
                'http://' in content or 'https://' in content,
                '[CQ:image' in content,
                '[CQ:video' in content,
                '[CQ:file' in content,
                '[CQ:record' in content,
                content.startswith('[CQ:')
            ])

            msg_type = "MEDIA" if is_media else "TEXT"
            msg = f"用户 {nickname}({user_id}) 在群 {group_id} 撤回了 [{msg_type}]: {content}"

            # 写入日志文件
            self.log_event("RECALL", msg)

        except Exception as e:
            self.logger.error(f"处理撤回事件失败: {e}")

    async def generate_and_send_ranking(self, group_id: str):
        """生成并发送排行榜"""
        try:
            # 从数据库获取今日统计
            stats = await self.db.get_today_message_stats(group_id)

            # 调试：检查数据
            debug_info = await self.db.get_today_message_stats_debug(group_id)
            self.logger.info(f"排行榜数据调试: 总消息={debug_info['total_today']}, 查询日期={debug_info['query_date']}")

            # 处理数据：确保有昵称，使用最新的昵称
            processed_stats = {}
            for user_id, count, nickname in stats:
                # 如果没有昵称，用QQ号代替
                display_name = nickname.strip() if nickname else str(user_id)
                # 如果昵称为空或只有空格，用QQ号
                if not display_name:
                    display_name = str(user_id)
                processed_stats[user_id] = (count, display_name)

            # 转换为列表并按次数排序
            final_stats = [(user_id, count, name) for user_id, (count, name) in processed_stats.items()]
            final_stats.sort(key=lambda x: x[1], reverse=True)

            if not final_stats:
                await self.ws_client.send_group_message(
                    group_id,
                    "今日暂无发言记录，无法生成排行榜。"
                )
                return

            # 生成排行榜图片
            image_data = self.ranking_generator.generate_ranking_image(final_stats, group_id)
            await self.ws_client.send_group_image(group_id, image_data)

        except Exception as e:
            self.logger.error(f"生成排行榜失败: {e}", exc_info=True)
            await self.ws_client.send_group_message(
                group_id,
                f"生成排行榜失败: {str(e)}"
            )

    async def _sign_check_loop(self):
        """后台循环：精确到秒的对齐检查，等待 WS 连接就绪后再执行"""
        self.logger.info("打卡时间检查任务已启动，正在同步时间...")

        # 时间偏差检测：对比本地时间和 HTTP 服务器时间
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://api.siliconflow.cn/v1/models", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    server_date = resp.headers.get("Date", "")
                    if server_date:
                        from email.utils import parsedate_to_datetime as _parse_dt
                        server_dt = _parse_dt(server_date)
                        local_dt = datetime.now(server_dt.tzinfo) if server_dt.tzinfo else datetime.now()
                        drift = (local_dt - server_dt.replace(tzinfo=None)).total_seconds()
                        self.logger.info(f"时间同步: 本地偏移 {drift:+.1f}秒 {'(偏快)' if drift > 2 else '(偏慢)' if drift < -2 else '(正常)'}")
        except Exception:
            self.logger.info("时间同步跳过（网络不可用）")

        # 等待 WebSocket 连接建立，最多等 30 秒
        for _ in range(30):
            if self.ws_client.websocket is not None:
                break
            await asyncio.sleep(1)
        else:
            self.logger.error("打卡任务等待 WebSocket 超时，放弃本次启动时打卡")

        self.logger.info("WebSocket 就绪，打卡检查循环开始")

        while True:
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")

                # 今日目标时刻
                today_target = now.replace(
                    hour=self.auto_sign_hour,
                    minute=self.auto_sign_minute,
                    second=0,
                    microsecond=0
                )

                if not self.auto_sign_enabled:
                    await asyncio.sleep(10)
                    continue

                # === 判断是否需要打卡 ===
                if self.sign_last_date == today_str:
                    # 今日已打过 → 跳过，等明天
                    target_tomorrow = today_target + timedelta(days=1)
                    seconds_until = (target_tomorrow - now).total_seconds()
                    await asyncio.sleep(max(1, seconds_until))
                    continue

                elif self.sign_last_date is None or self.sign_last_date < today_str:
                    if now < today_target:
                        # 粗睡到目标前12秒
                        coarse = (today_target - now).total_seconds() - 12
                        if coarse > 0:
                            await asyncio.sleep(coarse)
                        # 精确等待到目标前8秒，配合宽窗口覆盖时钟偏差
                        fire_start = today_target - timedelta(seconds=8)
                        remaining = (fire_start - datetime.now()).total_seconds()
                        if remaining > 0:
                            await asyncio.get_running_loop().run_in_executor(None, time.sleep, remaining)

                    # 确认连接
                    if self.ws_client.websocket is None:
                        self.logger.warning("打卡时 WebSocket 未连接，1秒后重试")
                        await asyncio.sleep(1)
                        continue

                    self.sign_last_date = today_str
                    self._save_sign_last_date(today_str)

                    # 宽窗口覆盖：6次请求，间隔4秒，从T-8到T+12覆盖20秒窗口
                    # 适应系统时钟偏差和网络延迟
                    burst_count = 6
                    burst_interval = 4
                    self.logger.info(f"打卡窗口开始，{burst_count}次请求覆盖{burst_count * burst_interval}秒窗口")
                    self.logger.info(f"本地时间: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}，目标: {today_target.strftime('%H:%M:%S')}")

                    sign_tasks = []
                    for i in range(burst_count):
                        if i > 0:
                            await asyncio.sleep(burst_interval)
                        actual = datetime.now()
                        drift = (actual - today_target).total_seconds()
                        self.logger.info(f"第 {i+1}/{burst_count} 次打卡 (T{drift:+.1f}s)")
                        sign_tasks.append(asyncio.create_task(self._do_auto_sign()))

                    for task in sign_tasks:
                        try:
                            await asyncio.wait_for(task, timeout=15)
                        except asyncio.TimeoutError:
                            self.logger.warning("某次打卡任务超时")
                        except Exception as e:
                            self.logger.error(f"打卡任务异常: {e}")

                    # 发送黄历
                    huangli = self.tools.get_huangli()
                    for group_id in (self._cached_group_list or []):
                        await self.ws_client.send_group_message(group_id, huangli)
                    self.logger.info("今日黄历已发送")

                    # 跳到明天目标时刻
                    next_target = today_target + timedelta(days=1)
                    sleep_seconds = (next_target - datetime.now()).total_seconds()
                    await asyncio.sleep(max(1, sleep_seconds))

            except Exception as e:
                self.logger.error(f"打卡检查异常: {e}", exc_info=True)
                # 不盲等固定时间，立即重新进入循环重新计算目标时刻
                await asyncio.sleep(1)

    async def _cleanup_loop(self):
        """后台循环：每天凌晨3点清理过期消息"""
        self.logger.info("消息清理任务已启动")

        while True:
            try:
                now = datetime.now()
                # 计算到下次凌晨3点的秒数
                next_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if now >= next_3am:
                    next_3am += timedelta(days=1)
                sleep_seconds = (next_3am - now).total_seconds()
                await asyncio.sleep(max(60, sleep_seconds))

                self.logger.info("执行消息清理...")
                try:
                    await self.db.cleanup_old_messages()
                    self.logger.info("消息清理完成")
                except Exception as e:
                    self.logger.error(f"消息清理失败: {e}")

                # 定期清理过期的速率限制记录
                self._prune_rate_records()

            except Exception as e:
                self.logger.error(f"清理检查异常: {e}")
                await asyncio.sleep(60)

    async def _prefetch_group_list(self):
        """预先获取群列表缓存"""
        try:
            if self.auto_sign_groups:
                # 使用配置的群列表
                self._cached_group_list = [str(g) for g in self.auto_sign_groups]
            else:
                # 获取所有群
                groups = await self.ws_client.get_group_list()
                self._cached_group_list = [str(g["group_id"]) for g in groups]
            self.logger.info(f"群列表已缓存，共 {len(self._cached_group_list)} 个群")
        except Exception as e:
            self.logger.error(f"预获取群列表失败: {e}")

    async def _do_auto_sign(self):
        """执行自动打卡（使用启动时缓存的群列表）"""
        try:
            self.logger.info("定时打卡开始执行")
            groups_to_sign = self._cached_group_list

            if groups_to_sign:
                self.logger.info(f"开始打卡，共 {len(groups_to_sign)} 个群: {groups_to_sign}")
                self.log_event("AUTO_SIGN", f"定时打卡开始，共 {len(groups_to_sign)} 个群")

                async def sign_once(group_id: str) -> tuple[str, bool]:
                    try:
                        success = await self.ws_client.send_group_sign(str(group_id))
                        return group_id, success
                    except Exception as e:
                        self.logger.error(f"群 {group_id} 打卡异常: {e}")
                        return group_id, False

                results = await asyncio.gather(*(sign_once(group_id) for group_id in groups_to_sign))

                success_count = 0
                fail_count = 0
                for group_id, success in results:
                    if success:
                        success_count += 1
                        self.logger.info(f"群 {group_id} 打卡成功")
                        self.log_event("AUTO_SIGN", f"群 {group_id} 打卡成功")
                    else:
                        fail_count += 1
                        self.logger.warning(f"群 {group_id} 打卡失败")

                self.logger.info(f"打卡完成: 成功 {success_count}, 失败 {fail_count}")
                self.log_event("AUTO_SIGN", f"打卡完成: 成功 {success_count}, 失败 {fail_count}")
            else:
                self.logger.warning("定时打卡触发，但没有找到任何群")

        except Exception as e:
            self.logger.error(f"定时打卡执行异常: {e}")

    async def _proactive_loop(self):
        """后台循环：7:00-23:59 随机向已私聊过的用户主动发消息（每人最多 2 条，回复后重置）"""
        # 启动后随机延迟 30min-2h，避免重启即推送
        startup_delay = random.randint(self.proactive_interval_min, self.proactive_interval_max)
        self.logger.info(f"主动推送任务已启动，{startup_delay // 60} 分钟后首次检查")
        await asyncio.sleep(startup_delay)

        while True:
            try:
                if not self.proactive_enabled:
                    await asyncio.sleep(300)
                    continue

                now = datetime.now()
                if 1 <= now.hour < 5:
                    self.logger.info(f"主动推送休眠 (凌晨 {now.hour}:{now.minute:02d})，等待到 05:00")
                    next_5 = now.replace(hour=5, minute=0, second=0, microsecond=0)
                    await asyncio.sleep(max(60, (next_5 - now).total_seconds()))
                    continue

                # 选目标：有画像、未封禁、未超 2 条未回复、且最近 2 小时内未推送
                candidates = []
                for uid in self.auto_profiles:
                    if uid == self.bot_qq:
                        continue
                    if self.is_user_blocked(uid):
                        continue
                    ps = self.proactive_state.get(uid, {"sent": 0, "replied": True})
                    # 已回复的可以再推，未回复的允许最多 2 条
                    if not (ps.get("replied", True) or ps.get("sent", 0) < 2):
                        continue
                    # 检查最近一次推送时间（2 小时内不重复推）
                    last = ps.get("last_sent", 0)
                    if last > 0 and (time.time() - last) < 7200 and ps.get("sent", 0) >= 2 and not ps.get("replied", False):
                        continue
                    candidates.append(uid)

                if candidates:
                    target = random.choice(candidates)
                    self.proactive_target = target
                    ps = self.proactive_state.get(target, {"sent": 0, "replied": True, "last_sent": 0})
                    if ps.get("replied", True):
                        ps = {"sent": 0, "replied": False, "last_sent": 0}
                    ps["sent"] += 1
                    ps["last_sent"] = time.time()
                    self.proactive_state[target] = ps
                    self._save_proactive_state()

                    # 生成时间情景化的主动消息
                    profile = self.auto_profiles.get(target, {})
                    name = profile.get("name", "朋友")
                    interests = profile.get("interests", "")
                    now = datetime.now()
                    hour = now.hour
                    if 5 <= hour < 8:
                        vibe = "清晨时段，可以发早安问候或聊聊今天的计划，语气清新慵懒"
                    elif 8 <= hour < 12:
                        vibe = "上午时段，可以聊聊工作/学习，或者分享有趣的事"
                    elif 12 <= hour < 14:
                        vibe = "午休时段，可以关心对方有没有好好吃饭休息"
                    elif 14 <= hour < 18:
                        vibe = "下午时段，可以吐槽下午的困倦或者分享摸鱼心得"
                    elif 18 <= hour < 21:
                        vibe = "傍晚晚间，可以聊聊晚饭、放松、今日趣事"
                    elif 21 <= hour < 24:
                        vibe = "深夜时段，可以聊聊心事、复盘今天、语气温柔慵懒"
                    else:
                        vibe = "凌晨时段，关心对方怎么还没睡，语气温柔带点担心"
                    messages = [
                        {"role": "system", "content": f"你是 Hina，一个真诚的朋友。现在是{now.strftime('%H:%M')}，{vibe}。你主动给 {name} 发消息。对方喜欢: {interests}。发一条自然的、不超过 50 字的消息。要符合当前时间段的情景和语气。不要像机器人，就像朋友突然想聊天了。纯文字。"},
                        {"role": "user", "content": "发一条主动消息"},
                    ]
                    reply = await self.api.chat_completion(messages, use_claude_persona=False, max_tokens=100)
                    if reply:
                        reply = reply.strip()
                        await self.ws_client.send_private_message(target, reply)
                        # 写入上下文，确保用户回复时 AI 知道对话前因
                        pvt_gid = f"private_{target}"
                        self.context_manager.add_message(pvt_gid, target, "assistant", reply)
                        self.logger.info(f"主动推送: → {target} ({name}) 第{ps['sent']}条")
                        self.log_event("PROACTIVE", f"→ {target} ({name})")
                else:
                    self.proactive_target = None

                # 随机间隔 30min-2h
                delay = random.randint(self.proactive_interval_min, self.proactive_interval_max)
                self.proactive_next_at = time.time() + delay
                await asyncio.sleep(delay)

            except Exception as e:
                self.logger.error(f"主动推送异常: {e}")
                await asyncio.sleep(600)

    async def start(self):
        """启动机器人"""
        self.logger.info("正在启动 BeiXAI Bot...")
        self.logger.info("AI模型: DeepSeek-V3.2 (伪装为 Claude Opus 4.7)")

        # 初始化数据库
        await self.db.init_db()
        await self.db.init_user_stats()
        self.logger.info("数据库初始化完成")

        # 启动定时清理任务（每天凌晨3点执行）
        asyncio.ensure_future(self._cleanup_loop())
        asyncio.ensure_future(self._proactive_loop())

        # 加载用户封禁列表
        self._load_blocked_users()
        self.logger.info(f"已加载 {len(self.blocked_users)} 个封禁用户")

        # 加载用户分级、配额、自动画像
        self._load_user_tiers()
        self._load_daily_costs()
        self._load_total_costs()
        self._load_auto_profiles()
        self._load_proactive_state()
        self.logger.info(f"已加载 {len(self.user_tiers)} 个用户分级, {len(self.daily_user_costs)} 条配额记录")

        # 启动 Web 管理后台
        if self.web_enabled:
            self.admin_server = AdminServer(self.web_host, self.web_port, self.web_token)
            self.admin_server.bot = self
            self.admin_server.blocked_users = self.blocked_users
            self.admin_server.log_handler = self.memory_log_handler
            self.admin_server.event_log_path = self.event_log_path
            await self.admin_server.start()
            self.logger.info(f"管理后台已启动: http://{self.web_host}:{self.web_port}")

        # 获取bot QQ号（从配置文件读取）
        self.bot_qq = str(self.config["bot"].get("bot_qq", ""))
        if not self.bot_qq:
            self.logger.warning("未配置bot_qq，请在config.json中设置bot.bot_qq字段")
            self.bot_qq = "123456789"  # 默认值

        self.message_handler = MessageHandler(self.bot_qq)
        self.logger.info(f"Bot QQ号: {self.bot_qq}")
        self.logger.info(f"上下文轮数: {self.config['bot'].get('context_rounds', 7)}")
        self.logger.info(f"幸运用户: {self.config['bot'].get('lucky_user', '未设置')}")
        self.logger.info(f"硬检测模式: {'严格模式' if self.hard_check_enabled else '关闭'}")
        self.logger.info(f"AI速率限制: {self.rate_limit}次/分钟")

        # 定时打卡配置
        if self.auto_sign_enabled:
            # 启动时立即拿一遍群列表，写入配置文件，之后直接用配置不再去NapCat拿
            if not self.auto_sign_groups:
                self.logger.info("配置文件无群列表，正在从NapCat获取...")
                try:
                    raw_groups = await self.ws_client.get_group_list()
                    self.auto_sign_groups = [str(g["group_id"]) for g in raw_groups]
                    self.config["auto_sign"]["groups"] = self.auto_sign_groups
                    # 保存到独立缓存文件，不覆盖用户配置
                    Path("data").mkdir(parents=True, exist_ok=True)
                    cache_file = Path("data/cached_groups.json")
                    cache_file.write_text(json.dumps(self.auto_sign_groups, ensure_ascii=False), encoding="utf-8")
                    self.logger.info(f"已获取并缓存 {len(self.auto_sign_groups)} 个群: {self.auto_sign_groups}")
                except Exception as e:
                    self.logger.error(f"获取群列表失败: {e}")
                    self.auto_sign_groups = []

            target_groups = self.auto_sign_groups if self.auto_sign_groups else "所有群"
            self.logger.info(f"定时打卡: 启用, 时间: {self.auto_sign_hour:02d}:{self.auto_sign_minute:02d}, 群: {target_groups}")
            self._cached_group_list = [str(g) for g in self.auto_sign_groups]
            # 启动打卡时间检查后台任务 + 看门狗
            asyncio.ensure_future(self._sign_check_loop())
        else:
            self.logger.info("定时打卡: 关闭")

        # 设置消息处理器
        self.ws_client.set_message_handler(self.handle_message)

        # 启动WebSocket客户端
        self.logger.info("正在连接到 NapCat...")
        await self.ws_client.start()

    async def stop(self):
        """停止机器人"""
        self.logger.info("正在停止 BeiXAI Bot...")
        self.ranking_generator.shutdown()
        await self.ws_client.stop()


async def main():
    bot = BeiXAIBot()

    # 创建任务
    tasks = [
        asyncio.create_task(bot.start()),
    ]
    # 定时打卡任务已通过 _schedule_sign_task 在 start() 中调度，不再需要单独任务

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n正在停止 Bot...")
    finally:
        # 取消所有待完成的任务
        for task in tasks:
            if not task.done():
                task.cancel()
        # 等待任务真正取消完成
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # 确保 WebSocket 连接正确关闭
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
