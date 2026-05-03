import json
import time
import re
from pathlib import Path
from collections import OrderedDict


class UserMemory:
    """用户长期记忆系统 — 潜移默化记住用户偏好/性格/事件"""

    def __init__(self, storage_dir: str = "data/memories"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict = {}  # {user_id: memory_data}

    def _path(self, user_id: str) -> Path:
        return self.storage_dir / f"{user_id}.json"

    def load(self, user_id: str) -> dict:
        if user_id in self._cache:
            return self._cache[user_id]
        try:
            path = self._path(user_id)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._cache[user_id] = data
                return data
        except Exception:
            pass
        data = {"facts": [], "summary": "", "total_interactions": 0, "last_updated": ""}
        self._cache[user_id] = data
        return data

    def save(self, user_id: str, data: dict = None):
        if data is None:
            data = self._cache.get(user_id, {"facts": [], "summary": "", "total_interactions": 0})
        self._cache[user_id] = data
        try:
            self._path(user_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def add_fact(self, user_id: str, fact_type: str, content: str, confidence: float = 0.5):
        """添加一条记忆事实，自动去重和合并"""
        data = self.load(user_id)
        now = time.strftime("%Y-%m-%d %H:%M")
        # 去重：相似内容只保留置信度更高的
        for f in data["facts"]:
            if self._similar(f["content"], content):
                f["confidence"] = max(f["confidence"], confidence)
                f["last_seen"] = now
                f["count"] = f.get("count", 1) + 1
                self.save(user_id, data)
                return
        data["facts"].append({
            "type": fact_type,
            "content": content,
            "confidence": confidence,
            "count": 1,
            "created": now,
            "last_seen": now,
        })
        # 限制最多 200 条，旧的低置信度优先淘汰
        if len(data["facts"]) > 200:
            data["facts"].sort(key=lambda f: f.get("confidence", 0) * f.get("count", 1))
            data["facts"] = data["facts"][-200:]
        self.save(user_id, data)

    def _similar(self, a: str, b: str) -> bool:
        """简单相似度检查"""
        a, b = a.strip().lower(), b.strip().lower()
        if a == b:
            return True
        if len(a) > 4 and len(b) > 4 and (a in b or b in a):
            return True
        # 共同字符超过 70%
        common = len(set(a) & set(b))
        return common > 0 and common / max(len(set(a)), len(set(b)), 1) > 0.7

    def get_relevant(self, user_id: str, context: str, max_facts: int = 8) -> list:
        """根据当前上下文检索相关记忆"""
        data = self.load(user_id)
        facts = data.get("facts", [])
        if not facts:
            return []

        ctx_keywords = set(re.findall(r"[一-鿿]{2,4}", context.lower()))
        scored = []
        for f in facts:
            score = f.get("confidence", 0.5) * f.get("count", 1) * 0.5
            # 关键词匹配加分
            f_words = set(re.findall(r"[一-鿿]{2,4}", f["content"].lower()))
            overlap = len(ctx_keywords & f_words)
            score += overlap * 2
            # 最近的记忆加分
            try:
                days_ago = (time.time() - time.mktime(time.strptime(f.get("last_seen", "2000-01-01"), "%Y-%m-%d %H:%M"))) / 86400
                score += max(0, 5 - days_ago) * 0.5
            except Exception:
                pass
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:max_facts] if _ > 0.5]

    def get_summary(self, user_id: str) -> str:
        """获取用户记忆摘要"""
        data = self.load(user_id)
        return data.get("summary", "")

    def set_summary(self, user_id: str, summary: str):
        data = self.load(user_id)
        data["summary"] = summary
        data["last_updated"] = time.strftime("%Y-%m-%d %H:%M")
        self.save(user_id, data)

    def increment_interactions(self, user_id: str):
        data = self.load(user_id)
        data["total_interactions"] = data.get("total_interactions", 0) + 1
        self.save(user_id, data)

    def get_stats(self, user_id: str) -> dict:
        data = self.load(user_id)
        facts_by_type = {}
        for f in data.get("facts", []):
            t = f["type"]
            facts_by_type[t] = facts_by_type.get(t, 0) + 1
        return {
            "total_facts": len(data.get("facts", [])),
            "total_interactions": data.get("total_interactions", 0),
            "has_summary": bool(data.get("summary", "")),
            "last_updated": data.get("last_updated", ""),
            "facts_by_type": facts_by_type,
        }

    def build_memory_prompt(self, user_id: str, current_msg: str) -> str:
        """构建注入到系统提示词的记忆片段"""
        relevant = self.get_relevant(user_id, current_msg, max_facts=8)
        if not relevant:
            return ""

        lines = ["\n\n[你对这个用户的了解（从长期记忆中检索）]"]
        for f in relevant:
            tag = {"preference": "喜欢", "dislike": "不喜欢", "event": "经历",
                   "personality": "性格", "habit": "习惯", "skill": "擅长",
                   "goal": "目标", "fact": "信息"}.get(f["type"], f["type"])
            lines.append(f"- {tag}: {f['content']}")
        return "\n".join(lines)
