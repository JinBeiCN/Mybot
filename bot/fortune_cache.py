from datetime import datetime
from typing import Dict, Optional


class FortuneCache:
    def __init__(self):
        # 存储每个用户当天的运势 {user_id: {"date": "YYYY-MM-DD", "fortune": "运势内容"}}
        self.cache: Dict[str, Dict[str, str]] = {}

    def get_fortune(self, user_id: str) -> Optional[str]:
        """获取用户今日运势（如果存在且未过期）"""
        if user_id not in self.cache:
            return None

        cached = self.cache[user_id]
        current_date = datetime.now().strftime("%Y-%m-%d")

        # 检查是否是今天的运势
        if cached.get("date") == current_date:
            return cached.get("fortune")

        return None

    def set_fortune(self, user_id: str, fortune: str):
        """设置用户今日运势"""
        current_date = datetime.now().strftime("%Y-%m-%d")
        self.cache[user_id] = {
            "date": current_date,
            "fortune": fortune
        }

    def clear_fortune(self, user_id: str) -> bool:
        """清除用户今日运势缓存，返回是否清除成功"""
        if user_id in self.cache:
            current_date = datetime.now().strftime("%Y-%m-%d")
            if self.cache[user_id].get("date") == current_date:
                del self.cache[user_id]
                return True
        return False

    def has_fortune(self, user_id: str) -> bool:
        """检查用户今日是否有运势缓存"""
        if user_id not in self.cache:
            return False
        current_date = datetime.now().strftime("%Y-%m-%d")
        return self.cache[user_id].get("date") == current_date

    def should_refresh(self) -> bool:
        """检查是否应该刷新运势（中午12点后）"""
        now = datetime.now()
        return now.hour >= 12
