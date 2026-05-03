from typing import Dict, List
from collections import defaultdict, deque


# 分级上下文轮数映射 (tier → max_rounds)
TIER_ROUNDS = {1: 100, 2: 200, 3: 500}
# Tier 3 自动压缩阈值（字符数，约 200k tokens）
COMPRESS_THRESHOLD = 400_000


class ContextManager:
    def __init__(self):
        self.contexts: Dict[str, Dict[str, deque]] = defaultdict(lambda: defaultdict(deque))

    def add_message(self, group_id: str, user_id: str, role: str, content: str):
        key = f"{group_id}_{user_id}"
        self.contexts[group_id][user_id].append({"role": role, "content": content})

    def get_context(self, group_id: str, user_id: str) -> List[Dict[str, str]]:
        return list(self.contexts[group_id][user_id])

    def clear_context(self, group_id: str, user_id: str):
        if group_id in self.contexts and user_id in self.contexts[group_id]:
            self.contexts[group_id][user_id].clear()

    def get_messages_for_api(self, group_id: str, user_id: str, system_prompt: str = None) -> List[Dict[str, str]]:
        """获取格式化消息（向后兼容，默认 tier 1）"""
        return self.get_messages_for_tier(group_id, user_id, system_prompt, tier=1, summarizer=None)

    def get_messages_for_tier(
        self, group_id: str, user_id: str,
        system_prompt: str = None, tier: int = 1,
        summarizer=None,
    ) -> List[Dict[str, str]]:
        """按用户分级获取上下文消息

        tier 1: 最近 50 轮 (100 条消息)
        tier 2: 最近 100 轮 (200 条消息)
        tier 3: 全部上下文，超过阈值自动压缩旧消息
        """
        max_rounds = TIER_ROUNDS.get(tier, 50)
        full_context = self.get_context(group_id, user_id)

        if tier < 3:
            # 固定轮数限制
            context = full_context[-max_rounds * 2:] if len(full_context) > max_rounds * 2 else full_context
        else:
            # Tier 3: 完整上下文，超过阈值压缩
            total_chars = sum(len(m.get("content", "")) for m in full_context)
            if total_chars > COMPRESS_THRESHOLD and summarizer:
                context = self._compress_context(full_context, summarizer)
            else:
                context = full_context[-max_rounds * 2:] if len(full_context) > max_rounds * 2 else full_context

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(context)
        return messages

    def _compress_context(
        self, context: List[Dict[str, str]], summarizer
    ) -> List[Dict[str, str]]:
        """压缩上下文：保留最近 100 轮，更早的压缩为摘要"""
        keep_recent = 200  # 100 轮 * 2
        if len(context) <= keep_recent:
            return context

        old_part = context[:-keep_recent]
        recent_part = context[-keep_recent:]

        # 构建旧对话文本
        old_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}"
            for m in old_part
        )
        summary = summarizer(old_text)

        compressed = [{"role": "system", "content": f"[历史对话摘要] {summary}"}]
        compressed.extend(recent_part)
        return compressed

    def get_context_stats(self, group_id: str, user_id: str) -> dict:
        """获取上下文统计信息"""
        ctx = self.get_context(group_id, user_id)
        total_chars = sum(len(m.get("content", "")) for m in ctx)
        return {
            "rounds": len(ctx) // 2,
            "total_chars": total_chars,
            "approx_tokens": total_chars // 2,
        }
