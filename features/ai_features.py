import asyncio
import json
import html
import re
from collections import Counter
from datetime import datetime
from typing import Optional, Tuple
from bot.database import Database
from bot.context_manager import ContextManager
from bot.fortune_cache import FortuneCache
from utils.siliconflow_api import SiliconFlowAPI
from utils.helpers import format_message_list, generate_daily_seed
from utils.ranking_generator import RankingGenerator
import random


class AIFeatures:
    def __init__(self, api: SiliconFlowAPI, db: Database, context_manager: ContextManager, lucky_user: str):
        self.api = api
        self.db = db
        self.context_manager = context_manager
        self.fortune_cache = FortuneCache()
        self.lucky_user = lucky_user
        self.cost_tracker = None  # 由 main.py 注入: fn(user_id, cost)

    async def chat(self, group_id: str, user_id: str, message: str) -> str:
        return await self.chat_with_profile(group_id, user_id, message, None)

    async def chat_with_profile(
        self, group_id: str, user_id: str, message: str,
        user_profile: dict = None, tier: int = 1, private_chat: bool = False,
    ) -> str:
        """AI对话功能（带用户画像、分级上下文、私聊模式）"""
        try:
            professional_mode = message.strip().startswith("#")
            if professional_mode:
                message = message.strip()[1:].strip()

            self.context_manager.add_message(group_id, user_id, "user", message)

            # 分级上下文（tier 3 需要 summarizer）
            summarizer = self._summarize_for_compression if tier >= 3 else None
            messages = self.context_manager.get_messages_for_tier(
                group_id, user_id, system_prompt=None, tier=tier, summarizer=summarizer
            )

            # 估算 token
            input_text = str(messages)
            input_tokens = len(input_text) / 2

            # 系统提示词
            system_prompt = self.api.get_system_prompt(
                user_profile, professional_mode=professional_mode, private_chat=private_chat
            )

            response = await self.api.chat_completion(messages, system_prompt=system_prompt)

            if response:
                output_tokens = len(response) / 2
                input_cost = (input_tokens / 1_000_000) * 60
                output_cost = (output_tokens / 1_000_000) * 225
                total_cost = input_cost + output_cost

                # 累计费用
                if not hasattr(self.context_manager, 'total_cost'):
                    self.context_manager.total_cost = 0
                self.context_manager.total_cost += total_cost

                # 通知配额追踪器
                if self.cost_tracker:
                    self.cost_tracker(user_id, total_cost)

                self.context_manager.add_message(group_id, user_id, "assistant", response)

                if private_chat:
                    return response  # 私聊不显示计费信息
                cost_info = f"\n\n💰 本次: ${total_cost:.6f} | 累计: ${self.context_manager.total_cost:.6f}"
                return response + cost_info
            else:
                return "抱歉，我现在无法回应。请稍后再试。"
        except Exception as e:
            return f"对话失败: {str(e)}"

    def _summarize_for_compression(self, text: str) -> str:
        """压缩上下文用的简要摘要"""
        # 取前 2000 字符做摘要，避免触发 API
        snippet = text[:2000]
        lines = [l for l in snippet.split("\n") if l.strip()]
        topics = set()
        for line in lines:
            for word in re.findall(r"[一-鿿]{2,4}", line):
                if len(topics) < 10:
                    topics.add(word)
        return f"之前讨论了: {', '.join(topics)}" if topics else "之前的对话内容"

    async def summarize_chat(self, group_id: str, limit: int = 50) -> str:
        """总结聊天记录"""
        try:
            messages = await self.db.get_recent_messages(group_id, limit)
            if not messages:
                return "暂无聊天记录可供总结。"

            formatted = format_message_list(messages)
            summary = await self.api.generate_summary(formatted)

            if summary:
                return f"📝 聊天记录总结：\n\n{summary}"
            else:
                return "总结生成失败，请稍后再试。"
        except Exception as e:
            return f"总结失败: {str(e)}"

    async def summarize_today_image(self, group_id: str) -> Tuple[bool, str]:
        """生成今日群信息总结图片，返回(是否成功, 图片数据或错误信息)"""
        try:
            # 获取今日统计数据
            debug_info = await self.db.get_today_message_stats_debug(group_id)
            today_date = debug_info["query_date"]
            total_messages = debug_info["total_today"]
            user_stats = debug_info["user_stats"]

            if total_messages == 0:
                return (False, "今日暂无发言记录")

            # 获取今日聊天记录（包含 extra）
            message_rows = await self.db.get_today_messages_with_extra(group_id, 200)
            if not message_rows:
                return (False, "获取聊天记录失败")

            text_messages = []
            image_ocr_results = []
            max_images = 10  # 限制最多处理10张图片

            # 先收集所有需要 OCR 的图片信息
            images_to_ocr = []
            img_idx = 0
            for user_id, message, timestamp, nickname, extra_json in message_rows:
                name = nickname if nickname else user_id
                text_messages.append((user_id, f"{name}: {message}", timestamp))

                extra = {}
                if extra_json:
                    try:
                        extra = json.loads(extra_json)
                    except Exception:
                        extra = {}

                for image in extra.get("images", []):
                    if len(images_to_ocr) >= max_images:
                        break
                    image_url = html.unescape(image.get("url", ""))
                    if not image_url:
                        continue
                    img_idx += 1
                    images_to_ocr.append((img_idx, name, image_url))

            # 并发执行所有 OCR 请求
            if images_to_ocr:
                ocr_tasks = [
                    self.api.ocr_image_url(url)
                    for _, _, url in images_to_ocr
                ]
                ocr_results = await asyncio.gather(*ocr_tasks, return_exceptions=True)
                for (img_idx, name, _), ocr_text in zip(images_to_ocr, ocr_results):
                    if isinstance(ocr_text, str) and ocr_text:
                        image_ocr_results.append(f"图片{img_idx}({name}): {ocr_text}")

            # 文本总结 + OCR 结果一起送给文本模型
            formatted = format_message_list(text_messages)
            if image_ocr_results:
                formatted += "\n\n【图片OCR结果】\n" + "\n".join(image_ocr_results)
            summary = await self.api.generate_summary(formatted)

            # 统计今日热词（只传纯文本，去掉昵称前缀）
            hot_words = self._extract_hot_words([msg for _, msg, _ in text_messages])

            # 构建内容
            content_lines = []
            content_lines.append(f"今日总消息: {total_messages} 条")
            content_lines.append(f"参与人数: {len(user_stats)} 人")
            content_lines.append(f"图片消息OCR: {len(image_ocr_results)} 张")
            content_lines.append("")

            content_lines.append("活跃排行榜:")
            for i, (user_id, count, nickname) in enumerate(user_stats[:3], 1):
                name = nickname if nickname else user_id
                content_lines.append(f"  #{i} {name}: {count} 条消息")
            content_lines.append("")

            if hot_words:
                content_lines.append("今日热词:")
                content_lines.append(f"  {' / '.join(hot_words[:5])}")
                content_lines.append("")

            if image_ocr_results:
                content_lines.append("图片识别摘要:")
                for line in image_ocr_results[:5]:
                    content_lines.append(f"  {line}")
                content_lines.append("")

            if summary:
                content_lines.append("今日概要:")
                summary_lines = summary.split("\n")
                for line in summary_lines:
                    if line.strip():
                        content_lines.append(f"  {line}")

            generator = RankingGenerator()
            title = f"📊 {today_date} 群信息总结"
            footer = f"BeiXAI Bot · 共 {total_messages} 条消息 · 图片OCR {len(image_ocr_results)} 张"

            image_data = generator.generate_summary_image(title, content_lines, footer)
            return (True, image_data)

        except Exception as e:
            return (False, f"生成失败: {str(e)}")

    def _extract_hot_words(self, messages: list[str]) -> list[str]:
        """提取今日主题词（偏讨论主题，过滤口头禅和昵称）"""
        stop_words = {
            # 常见口头禅
            "哈哈", "哈哈哈", "hhhh", "笑死", "666", "wwww", "草", "草", "操", "靠", "艹",
            # 常见无意义词
            "图片", "消息", "今天", "今日", "这个", "那个", "我们", "你们", "他们", "自己",
            "不是", "就是", "还是", "真的", "感觉", "已经", "没有", "什么", "怎么", "可以",
            "一下", "一个", "因为", "所以", "然后", "现在", "还有", "刚才", "其实", "应该",
            "这样", "那样", "这么", "那么", "可能", "或者", "但是", "而且", "如果", "虽然",
            # 常见问候/表情
            "你好", "晚安", "早上好", "午安", "在吗", "在不在", "嗯", "哦", "啊", "唉", "诶",
            # 英文常见词
            "lol", "www", "emm", "fff", "xxx", "yyy",
        }
        # 排除单字重复如 "哈哈哈哈哈"
        pattern_repeat1 = re.compile(r"^(.)\1{2,}$")

        counter = Counter()
        for msg in messages:
            if not msg:
                continue
            # 去掉CQ码和URL
            text = re.sub(r"\[[^\]]+\]", " ", msg)
            text = re.sub(r"https?://\S+", " ", msg)
            # 只保留中文词、英文词、数字
            text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", text)

            for token in re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{4,}|\d{4,}", text):
                token = token.strip()
                # 跳过太短的
                if len(token) < 2:
                    continue
                # 跳过纯数字
                if token.isdigit():
                    continue
                # 跳过全小写英文停用词
                if token.lower() in stop_words:
                    continue
                # 跳过重复字如 "哈哈哈"
                if pattern_repeat1.match(token):
                    continue
                counter[token] += 1

        # 出现2次以上才算主题词
        return [word for word, count in counter.most_common(10) if count >= 2]

    async def generate_fortune(self, user_id: str) -> str:
        """生成今日运势（带缓存）"""
        try:
            # 检查缓存
            cached_fortune = self.fortune_cache.get_fortune(user_id)
            if cached_fortune:
                return cached_fortune

            date = datetime.now().strftime("%Y-%m-%d")
            seed = generate_daily_seed(user_id, date)
            random.seed(seed)

            # 检查是否是幸运用户
            is_lucky = (user_id == self.lucky_user)

            if is_lucky:
                # 幸运用户固定高分 (9-10星)
                overall = random.randint(9, 10)
                love = random.randint(9, 10)
                career = random.randint(9, 10)
                wealth = random.randint(9, 10)
            else:
                # 普通用户随机分数 (1-10星)
                overall = random.randint(1, 10)
                love = random.randint(1, 10)
                career = random.randint(1, 10)
                wealth = random.randint(1, 10)

            lucky_num = random.randint(1, 99)

            # 生成简短的运势描述
            fortune_desc = self._get_fortune_desc(overall, is_lucky)

            result = f"🔮 今日运势 ({date})\n\n"
            result += f"综合运势: {'⭐' * overall} ({overall}/10)\n"
            result += f"爱情运势: {'💖' * love} ({love}/10)\n"
            result += f"事业运势: {'💼' * career} ({career}/10)\n"
            result += f"财运指数: {'💰' * wealth} ({wealth}/10)\n"
            result += f"幸运数字: {lucky_num}\n\n"
            result += f"💭 {fortune_desc}"

            # 缓存运势
            self.fortune_cache.set_fortune(user_id, result)

            return result
        except Exception as e:
            return f"运势生成失败: {str(e)}"

    def _get_fortune_desc(self, overall: int, is_lucky: bool) -> str:
        """根据运势分数生成简短描述"""
        if is_lucky:
            descs = [
                "今天是你的幸运日！万事顺心，好运连连！",
                "运势爆棚！把握机会，大展宏图！",
                "诸事大吉！今天做什么都会很顺利！"
            ]
        elif overall >= 8:
            descs = [
                "运势极佳！今天适合做重要决定。",
                "好运当头！把握机会，勇敢前行。",
                "运气不错！今天会有意外惊喜。"
            ]
        elif overall >= 6:
            descs = [
                "运势平稳，保持积极心态即可。",
                "今天适合稳扎稳打，不宜冒险。",
                "运势尚可，顺其自然就好。"
            ]
        elif overall >= 4:
            descs = [
                "运势一般，注意细节，避免失误。",
                "今天需要谨慎行事，三思而后行。",
                "运势平平，低调行事为宜。"
            ]
        else:
            descs = [
                "运势欠佳，建议今天多休息，少折腾。",
                "今天不太顺利，保持耐心，明天会更好。",
                "运势低迷，静待时机，不宜强求。"
            ]

        return random.choice(descs)

    async def generate_poem(self, theme: Optional[str] = None) -> str:
        """生成诗词"""
        try:
            if not theme:
                theme = "春天"

            poem = await self.api.generate_poem(theme)
            if poem:
                return f"📜 诗词创作\n主题: {theme}\n\n{poem}"
            else:
                return "诗词生成失败，请稍后再试。"
        except Exception as e:
            return f"诗词生成失败: {str(e)}"

    async def idiom_solitaire(self, idiom: str) -> str:
        """成语接龙"""
        try:
            result = await self.api.idiom_solitaire(idiom)
            if result:
                return f"🎯 成语接龙\n你的成语: {idiom}\n\n{result}"
            else:
                return "成语接龙失败，请稍后再试。"
        except Exception as e:
            return f"成语接龙失败: {str(e)}"

    def clear_context(self, group_id: str, user_id: str) -> str:
        """清除用户上下文"""
        self.context_manager.clear_context(group_id, user_id)
        return "✅ 已清除对话上下文"

    # ==================== 后门功能（管理员专用） ====================

    def set_fortune(self, user_id: str, stars: int) -> str:
        """设置用户自定义运势等级（管理员用）"""
        try:
            date = datetime.now().strftime("%Y-%m-%d")

            # 验证参数范围
            if not 1 <= stars <= 10:
                return "运势等级必须在 1-10 之间"

            # 根据等级生成描述
            fortune_desc = self._get_fortune_desc(stars, stars >= 8)

            # 生成运势（4项都设为指定等级）
            result = f"🔮 今日运势 ({date})\n\n"
            result += f"综合运势: {'⭐' * stars} ({stars}/10)\n"
            result += f"爱情运势: {'💖' * stars} ({stars}/10)\n"
            result += f"事业运势: {'💼' * stars} ({stars}/10)\n"
            result += f"财运指数: {'💰' * stars} ({stars}/10)\n"
            result += f"幸运数字: {random.randint(1, 99)}\n\n"
            result += f"💭 {fortune_desc}"

            self.fortune_cache.set_fortune(user_id, result)
            return f"✅ 已为用户 {user_id} 设置今日运势等级为 {stars} 星"

        except Exception as e:
            return f"设置运势失败: {str(e)}"

    def reroll_fortune(self, user_id: str) -> str:
        """重置用户运势缓存（清除缓存，用户可以自己重新抽取）"""
        try:
            if user_id in self.fortune_cache.cache:
                del self.fortune_cache.cache[user_id]
                return f"✅ 已重置用户 {user_id} 的今日运势，对方可以重新发送「运势」命令抽取"
            else:
                return f"用户 {user_id} 今日暂无运势记录，无需重置"

        except Exception as e:
            return f"重置运势失败: {str(e)}"

    async def comment_chat(self, group_id: str, message_id: str) -> str:
        """对聊天记录进行刻薄评论"""
        try:
            # 获取上下文
            context = await self.db.get_message_context(message_id, group_id, total_chars=500)

            if not context["quoted"]:
                return "找不到引用的消息，可能是已被撤回或不在数据库中"

            quoted = context["quoted"]
            before = context["before"]
            after = context["after"]

            # 构建上下文文本
            context_parts = []
            if before:
                context_parts.append(f"【上文】\n{before}")
            context_parts.append(f"【被引用消息】({quoted['nickname']})\n{quoted['message']}")
            if after:
                context_parts.append(f"【下文】\n{after}")

            full_context = "\n\n".join(context_parts)

            # 调用AI生成刻薄评论
            comment = await self.api.generate_roast_comment(full_context)

            if comment:
                return comment
            else:
                return "评论生成失败，请稍后再试"
        except Exception as e:
            return f"评论失败: {str(e)}"
