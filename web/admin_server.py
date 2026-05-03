import asyncio
import json
import logging
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from aiohttp import web


class MemoryLogHandler(logging.Handler):
    """内存日志处理器，缓存最近 N 条日志供 Web 查看"""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        entry = {
            "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        self.buffer.append(entry)

    def get_logs(self, count: int = 200) -> list:
        logs = list(self.buffer)
        return logs[-count:]


class AdminServer:
    def __init__(self, host: str, port: int, token: str):
        self.host = host
        self.port = port
        self.token = token
        self.app = web.Application()
        self.log_handler: MemoryLogHandler | None = None
        # 外部注入的共享状态
        self.bot = None
        self.blocked_users: dict = {}
        self.blocked_users_path = "data/blocked_users.json"
        self.event_log_path = "data/events.log"
        self.start_time = time.time()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/api/status", self.handle_api_status)
        self.app.router.add_get("/api/users", self.handle_api_users)
        self.app.router.add_post("/api/users/block", self.handle_api_block)
        self.app.router.add_post("/api/users/unblock", self.handle_api_unblock)
        self.app.router.add_get("/api/logs", self.handle_api_logs)
        self.app.router.add_get("/api/events", self.handle_api_events)
        self.app.router.add_get("/api/tiers", self.handle_api_tiers)
        self.app.router.add_post("/api/tiers/set", self.handle_api_tiers_set)
        self.app.router.add_get("/api/proactive/status", self.handle_api_proactive_status)
        self.app.router.add_post("/api/proactive/toggle", self.handle_api_proactive_toggle)
        self.app.router.add_post("/api/users/balance", self.handle_api_set_balance)
        self.app.router.add_post("/api/users/cost", self.handle_api_set_cost)
        self.app.router.add_post("/api/settings/rate", self.handle_api_set_rate)
        self.app.router.add_post("/api/settings/hardcheck", self.handle_api_set_hardcheck)
        self.app.router.add_post("/api/settings/sign", self.handle_api_set_sign)
        self.app.router.add_post("/api/settings/sign-toggle", self.handle_api_set_sign_toggle)
        self.app.router.add_get("/api/memory/{user_id}", self.handle_api_memory)
        self.app.router.add_get("/api/models", self.handle_api_models)
        self.app.router.add_post("/api/models/switch", self.handle_api_switch_model)
        self.app.router.add_post("/api/models/custom", self.handle_api_custom_model)

    async def handle_index(self, request: web.Request) -> web.Response:
        html_path = Path(__file__).parent / "templates" / "admin.html"
        text = html_path.read_text(encoding="utf-8")
        return web.Response(text=text, content_type="text/html", charset="utf-8")

    # ==================== API: 状态 ====================

    async def handle_api_status(self, request: web.Request) -> web.Response:

        uptime_seconds = int(time.time() - self.start_time)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        ws_connected = False
        group_count = 0
        if self.bot:
            ws_connected = self.bot.ws_client.websocket is not None
            group_count = len(getattr(self.bot, "_cached_group_list", []))

        proactive_enabled = False
        proactive_next_at = None
        proactive_target = None
        proactive_state_count = 0
        if self.bot:
            proactive_enabled = getattr(self.bot, "proactive_enabled", False)
            proactive_next_at = getattr(self.bot, "proactive_next_at", None)
            proactive_target = getattr(self.bot, "proactive_target", None)
            proactive_state_count = len(getattr(self.bot, "proactive_state", {}))

        # 倒计时
        countdown = 0
        if proactive_next_at and proactive_enabled:
            countdown = max(0, int(proactive_next_at - time.time()))

        return web.json_response({
            "uptime": uptime_str,
            "uptime_seconds": uptime_seconds,
            "ws_connected": ws_connected,
            "group_count": group_count,
            "blocked_users": len(self.blocked_users),
            "rate_limit": getattr(self.bot, "rate_limit", 10) if self.bot else 10,
            "auto_sign_enabled": getattr(self.bot, "auto_sign_enabled", False) if self.bot else False,
            "sign_last_date": getattr(self.bot, "sign_last_date", None) if self.bot else None,
            "sign_hour": getattr(self.bot, "auto_sign_hour", 0) if self.bot else 0,
            "sign_minute": getattr(self.bot, "auto_sign_minute", 0) if self.bot else 0,
            "sign_groups": len(getattr(self.bot, "auto_sign_groups", [])) if self.bot else 0,
            "model": getattr(self.bot, "api", None) and self.bot.api.model if self.bot else "N/A",
            "hard_check": getattr(self.bot, "hard_check_enabled", False) if self.bot else False,
            "proactive_enabled": proactive_enabled,
            "proactive_next_at": proactive_next_at,
            "proactive_target": proactive_target,
            "proactive_countdown": countdown,
            "proactive_active_users": proactive_state_count,
        })

    # ==================== API: 用户管理 ====================

    async def handle_api_users(self, request: web.Request) -> web.Response:

        users = []
        for user_id, blocked_at in self.blocked_users.items():
            users.append({
                "user_id": user_id,
                "blocked_at": blocked_at,
                "blocked_time": datetime.fromtimestamp(blocked_at).strftime("%Y-%m-%d %H:%M:%S"),
            })
        users.sort(key=lambda u: u["blocked_at"], reverse=True)
        return web.json_response({"users": users, "total": len(users)})

    async def handle_api_block(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
            user_id = str(body.get("user_id", "")).strip()
            if not user_id:
                return web.json_response({"error": "user_id required"}, status=400)

            if user_id in self.blocked_users:
                return web.json_response({"error": "already blocked", "user_id": user_id}, status=409)

            self.blocked_users[user_id] = time.time()
            self._save_blocked_users()
            return web.json_response({"success": True, "user_id": user_id})
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)

    async def handle_api_unblock(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
            user_id = str(body.get("user_id", "")).strip()
            if not user_id:
                return web.json_response({"error": "user_id required"}, status=400)

            removed = self.blocked_users.pop(user_id, None)
            if removed:
                self._save_blocked_users()
                return web.json_response({"success": True, "user_id": user_id})
            return web.json_response({"error": "not found", "user_id": user_id}, status=404)
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)

    # ==================== API: 日志 ====================

    async def handle_api_logs(self, request: web.Request) -> web.Response:

        count = min(int(request.query.get("count", 200)), 500)
        logs = self.log_handler.get_logs(count) if self.log_handler else []
        return web.json_response({"logs": logs, "total": len(logs)})

    async def handle_api_events(self, request: web.Request) -> web.Response:

        try:
            path = Path(self.event_log_path)
            events = []
            if path.exists():
                lines = path.read_text(encoding="utf-8").strip().split("\n")
                for line in lines[-200:]:
                    events.append(line)
            return web.json_response({"events": events, "total": len(events)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ==================== API: 用户分级 ====================

    async def handle_api_tiers(self, request: web.Request) -> web.Response:

        if not self.bot:
            return web.json_response({"tiers": [], "config": {}})

        tier_config = getattr(self.bot, "tier_config", {})
        user_tiers = getattr(self.bot, "user_tiers", {})
        daily_costs = getattr(self.bot, "daily_user_costs", {})

        users = []
        for user_id, tier in user_tiers.items():
            cost_record = daily_costs.get(str(user_id), {})
            users.append({
                "user_id": user_id,
                "tier": tier,
                "tier_name": tier_config.get(str(tier), {}).get("name", f"Lv.{tier}"),
                "daily_cost": round(cost_record.get("cost", 0), 4),
                "quota": tier_config.get(str(tier), {}).get("daily_quota", 0),
            })
        users.sort(key=lambda u: u["tier"], reverse=True)
        return web.json_response({"users": users, "config": tier_config})

    async def handle_api_tiers_set(self, request: web.Request) -> web.Response:

        try:
            body = await request.json()
            user_id = str(body.get("user_id", "")).strip()
            tier = int(body.get("tier", 1))
            if not user_id:
                return web.json_response({"error": "user_id required"}, status=400)
            if tier not in (1, 2, 3):
                return web.json_response({"error": "tier must be 1, 2, or 3"}, status=400)

            if self.bot:
                self.bot.set_user_tier(user_id, tier)
            return web.json_response({"success": True, "user_id": user_id, "tier": tier})
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid json"}, status=400)

    # ==================== API: 主动推送 ====================

    async def handle_api_proactive_status(self, request: web.Request) -> web.Response:
        if not self.bot:
            return web.json_response({"enabled": False, "countdown": 0})
        return web.json_response({
            "enabled": getattr(self.bot, "proactive_enabled", False),
            "next_at": getattr(self.bot, "proactive_next_at", None),
            "target": getattr(self.bot, "proactive_target", None),
            "countdown": max(0, int(getattr(self.bot, "proactive_next_at", 0) - time.time())) if self.bot.proactive_next_at else 0,
            "active_users": len(getattr(self.bot, "proactive_state", {})),
        })

    async def handle_api_proactive_toggle(self, request: web.Request) -> web.Response:
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            enabled = bool(body.get("enabled", True))
            self.bot.proactive_enabled = enabled
            return web.json_response({"success": True, "enabled": enabled})
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid json"}, status=400)

    # ==================== API: 自定义余额/已用 ====================

    async def handle_api_set_balance(self, request: web.Request) -> web.Response:
        """设置用户自定义每日限额（0=使用分级默认）"""
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            user_id = str(body.get("user_id", "")).strip()
            amount = float(body.get("amount", 0))
            if not user_id:
                return web.json_response({"error": "user_id required"}, status=400)
            key = str(user_id)
            today = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
            record = self.bot.daily_user_costs.get(key, {"cost": 0, "date": today})
            record["custom_quota"] = amount
            self.bot.daily_user_costs[key] = record
            self.bot._save_daily_costs()
            return web.json_response({"success": True, "user_id": user_id, "custom_quota": amount})
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid json"}, status=400)

    async def handle_api_set_cost(self, request: web.Request) -> web.Response:
        """设置用户今日已用额度"""
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            user_id = str(body.get("user_id", "")).strip()
            amount = float(body.get("amount", 0))
            if not user_id:
                return web.json_response({"error": "user_id required"}, status=400)
            key = str(user_id)
            today = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
            record = self.bot.daily_user_costs.get(key, {"cost": 0, "date": today})
            record["cost"] = amount
            record["date"] = today
            self.bot.daily_user_costs[key] = record
            self.bot._save_daily_costs()
            return web.json_response({"success": True, "user_id": user_id, "today_cost": amount})
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid json"}, status=400)

    # ==================== API: 仪表盘快捷设置 ====================

    async def handle_api_set_rate(self, request: web.Request) -> web.Response:
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            rate = int(body.get("rate", 10))
            if rate < 1: rate = 1
            self.bot.rate_limit = rate
            return web.json_response({"success": True, "rate": rate})
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid"}, status=400)

    async def handle_api_set_hardcheck(self, request: web.Request) -> web.Response:
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            enabled = bool(body.get("enabled", False))
            self.bot.hard_check_enabled = enabled
            return web.json_response({"success": True, "enabled": enabled})
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid"}, status=400)

    async def handle_api_set_sign(self, request: web.Request) -> web.Response:
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            h = int(body.get("hour", 0))
            m = int(body.get("minute", 0))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return web.json_response({"error": "invalid time"}, status=400)
            self.bot.auto_sign_hour = h
            self.bot.auto_sign_minute = m
            return web.json_response({"success": True, "hour": h, "minute": m})
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "invalid"}, status=400)

    async def handle_api_set_sign_toggle(self, request: web.Request) -> web.Response:
        if not self.bot:
            return web.json_response({"error": "bot not ready"}, status=503)
        try:
            body = await request.json()
            enabled = bool(body.get("enabled", True))
            self.bot.auto_sign_enabled = enabled
            return web.json_response({"success": True, "enabled": enabled})
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid"}, status=400)

    # ==================== API: 用户记忆 ====================

    async def handle_api_memory(self, request: web.Request) -> web.Response:
        user_id = request.match_info.get("user_id", "")
        if not self.bot or not hasattr(self.bot, "user_memory"):
            return web.json_response({"error": "not ready"}, status=503)
        data = self.bot.user_memory.load(user_id)
        stats = self.bot.user_memory.get_stats(user_id)
        return web.json_response({
            "user_id": user_id,
            "facts": data.get("facts", []),
            "summary": data.get("summary", ""),
            "total_interactions": stats["total_interactions"],
            "total_facts": stats["total_facts"],
            "last_updated": stats["last_updated"],
            "facts_by_type": stats["facts_by_type"],
        })

    # ==================== API: 模型管理 ====================

    async def handle_api_models(self, request: web.Request) -> web.Response:
        if not self.bot or not self.bot.api:
            return web.json_response({"models": [], "current": "", "ocr": ""})
        models = await self.bot.api.fetch_models()
        return web.json_response({
            "models": models,
            "current": self.bot.api.model,
            "ocr_current": self.bot.api.ocr_model,
            "custom_model": self.bot.api.custom_model or "",
            "custom_base_url": self.bot.api.custom_base_url or "",
        })

    async def handle_api_switch_model(self, request: web.Request) -> web.Response:
        if not self.bot or not self.bot.api:
            return web.json_response({"error": "not ready"}, status=503)
        try:
            body = await request.json()
            model_type = body.get("type", "chat")
            model_id = str(body.get("model_id", "")).strip()
            if not model_id:
                return web.json_response({"error": "model_id required"}, status=400)
            if model_type == "ocr":
                self.bot.api.ocr_model = model_id
            else:
                self.bot.api.model = model_id
            return web.json_response({"success": True, "type": model_type, "model": model_id})
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid"}, status=400)

    async def handle_api_custom_model(self, request: web.Request) -> web.Response:
        if not self.bot or not self.bot.api:
            return web.json_response({"error": "not ready"}, status=503)
        try:
            body = await request.json()
            custom_model = str(body.get("custom_model", "")).strip()
            custom_url = str(body.get("custom_url", "")).strip()
            self.bot.api.custom_model = custom_model
            self.bot.api.custom_base_url = custom_url
            return web.json_response({"success": True, "custom_model": custom_model, "custom_url": custom_url})
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid"}, status=400)

    # ==================== 持久化 ====================

    def _load_blocked_users(self) -> dict:
        try:
            path = Path(self.blocked_users_path)
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_blocked_users(self):
        try:
            Path(self.blocked_users_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.blocked_users_path).write_text(
                json.dumps(self.blocked_users, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logging.getLogger("AdminServer").error(f"保存封禁列表失败: {e}")

    # ==================== 启动 ====================

    async def start(self):
        self.blocked_users = self._load_blocked_users()
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logging.getLogger("AdminServer").info(f"管理后台已启动: http://{self.host}:{self.port}")


# ==================== 管理后台 HTML ====================

