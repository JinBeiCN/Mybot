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

    # ==================== 风格指纹 ====================

    def extract_style_fingerprint(self, messages: list[str]) -> dict:
        """从用户最近消息中提取语气风格指纹（纯统计，无 API 调用）"""
        if not messages:
            return {}
        total = len(messages)
        total_chars = sum(len(m) for m in messages)
        avg_len = total_chars / total

        # 标点偏好
        punct = {"，": 0, "。": 0, "！": 0, "？": 0, "~": 0, "…": 0, "...": 0}
        all_text = "".join(messages)
        for k in punct:
            punct[k] = all_text.count(k)

        # 高频短词（中文2-4字 + 常见口头禅）
        raw_words = re.findall(r"[一-鿿]{2,4}|[a-z]{3,}", all_text.lower())
        word_freq = {}
        for w in raw_words:
            word_freq[w] = word_freq.get(w, 0) + 1
        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:15]

        # 口语化指标
        oral_markers = ["草", "笑死", "救命", "确实", "绝了", "好吧", "行吧", "嗯", "啊这",
                        "哈哈", "hhhh", "绷不住", "难绷", "蚌埠", "哈哈哈哈", "www", "qwq", "orz"]
        oral_score = sum(all_text.count(m) for m in oral_markers)

        # 句长分布
        sentences = re.split(r"[。！？\n]", all_text)
        short_sentences = sum(1 for s in sentences if 1 <= len(s.strip()) <= 10)
        long_sentences = sum(1 for s in sentences if len(s.strip()) > 50)

        # emoji 频率
        emoji_count = len(re.findall(r"[一-鿿]{1,2}[️︎]*", all_text))  # 简略估算
        emoji_count += all_text.count("qwq") + all_text.count("orz") + all_text.count("www")

        # 整体语气倾向
        tone_markers = {"热情": ["哈哈", "！！", "好好好", "太棒", "开心", "嘿嘿", "耶"],
                        "冷静": ["嗯", "好的", "行", "可以", "👌", "了解"],
                        "毒舌": ["笑死", "绷不住", "难绷", "就这", "不是吧", "草"]}
        tone_scores = {}
        for tone, markers in tone_markers.items():
            tone_scores[tone] = sum(all_text.count(m) for m in markers)

        return {
            "avg_len": round(avg_len, 1),
            "total_messages": total,
            "top_words": top_words[:10],
            "oral_score": oral_score,
            "short_ratio": round(short_sentences / max(len(sentences), 1), 2),
            "long_ratio": round(long_sentences / max(len(sentences), 1), 2),
            "emoji_freq": round(emoji_count / max(total, 1), 1),
            "tone": max(tone_scores, key=tone_scores.get) if max(tone_scores.values()) > 0 else "中性",
            "punct_top": sorted(punct.items(), key=lambda x: x[1], reverse=True)[:3],
        }

    def set_style(self, user_id: str, fingerprint: dict):
        """存储风格指纹到用户记忆"""
        data = self.load(user_id)

        # 生成风格描述文本
        fp = fingerprint
        desc_lines = []
        if fp.get("avg_len", 0) < 20:
            desc_lines.append("消息很短")
        elif fp.get("avg_len", 0) > 60:
            desc_lines.append("消息较长、喜欢展开说")

        tone = fp.get("tone", "中性")
        tone_map = {"热情": "语气热烈外向", "冷静": "语气冷静克制", "毒舌": "语气毒舌有梗", "中性": "语气中性随意"}
        desc_lines.append(tone_map.get(tone, tone))

        if fp.get("oral_score", 0) > 10:
            desc_lines.append("口语化程度高、常用网络流行语")
        if fp.get("emoji_freq", 0) > 2:
            desc_lines.append("喜欢用表情/颜文字")

        top_words = fp.get("top_words", [])[:5]
        if top_words:
            words_str = "、".join([w[0] for w in top_words])
            desc_lines.append(f"高频词: {words_str}")

        desc = "；".join(desc_lines)

        # 去重更新 style 类型的记忆
        for f in data.get("facts", []):
            if f.get("type") == "style":
                f["content"] = desc
                f["confidence"] = 0.9
                f["count"] = f.get("count", 1) + 1
                f["last_seen"] = time.strftime("%Y-%m-%d %H:%M")
                f["_fingerprint"] = fingerprint
                self.save(user_id, data)
                return desc

        self.add_fact(user_id, "style", desc, confidence=0.8)
        # 附加指纹数据到刚才添加的 fact
        data = self.load(user_id)
        for f in data["facts"]:
            if f.get("type") == "style":
                f["_fingerprint"] = fingerprint
        self.save(user_id, data)
        return desc

    def build_style_prompt(self, user_id: str) -> str:
        """构建语气风格的提示词片段"""
        data = self.load(user_id)
        style_fact = None
        for f in data.get("facts", []):
            if f.get("type") == "style":
                style_fact = f
                break
        if not style_fact:
            return ""

        content = style_fact.get("content", "")
        fp = style_fact.get("_fingerprint", {})

        lines = ["\n\n[语气适配 — 你和对方聊天时可以参考以下风格]"]
        lines.append(f"对方说话特点: {content}")
        lines.append("你的回复应该在保持 Hina 人格的前提下，自然地靠近对方的语气节奏。")
        if fp.get("avg_len", 50) < 20:
            lines.append("对方消息短，你也不用回太长。")
        tone = fp.get("tone", "")
        if tone == "热情":
            lines.append("可以稍微热一点回应，不用太克制。")
        elif tone == "毒舌":
            lines.append("可以放开点吐槽，对方喜欢这种感觉。")
        elif tone == "冷静":
            lines.append("保持简洁，不用太热情。")
        return "\n".join(lines)
