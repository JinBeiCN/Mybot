from typing import Optional, Dict, Any, Tuple
from features.ai_features import AIFeatures
from features.tools import Tools
from features.fun_features import FunFeatures


class CommandRouter:
    def __init__(self, ai_features: AIFeatures, tools: Tools, fun_features: FunFeatures):
        self.ai_features = ai_features
        self.tools = tools
        self.fun_features = fun_features

    async def route_command(self, command: str, args: Optional[str], group_id: str, user_id: str, user_profile: Dict[str, Any] = None, tier: int = 1) -> Tuple[bool, str]:
        """路由命令到对应的处理器，返回(is_local: bool, response: str)
        - is_local=True 表示本地命令，不计入速率限制
        - is_local=False 表示AI命令，需要计入速率限制
        """
        command = command.strip()
        full_text = f"{command} {args}" if args else command

        # 本地命令（不计入速率）
        if command == "清除上下文":
            return (True, self.ai_features.clear_context(group_id, user_id))

        # 工具功能（不计入速率）
        elif command == "黄历":
            return (True, self.tools.get_huangli())

        elif command == "笑话":
            return (True, self.tools.get_joke())

        # 娱乐功能（不计入速率）
        elif command == "星座":
            if not args:
                return (True, "请提供星座名称，例如：@BeiXAI 星座 白羊座")
            return (True, self.fun_features.get_zodiac_fortune(args))

        elif command == "一言":
            return (True, self.fun_features.get_daily_quote())

        elif command == "帮助" or command == "help" or command == "菜单":
            return (True, self.fun_features.get_help())

        elif command == "信息":
            return (True, "🤖 BeiXAI Bot\n\n版本: 1.0.0\n开发者: JinBei")

        # 评论功能（计入速率）
        elif command == "评论" or command == "锐评":
            if not args:
                return (True, "请引用要评论的消息并 @BeiXAI 评论")
            return (False, await self.ai_features.comment_chat(group_id, args))

        # AI命令（计入速率）
        elif command == "总结" or command == "聊天记录":
            # 特殊处理：在 main.py 中生成图片
            return (False, "__GENERATE_SUMMARY_IMAGE__")

        # 运势匹配
        elif command == "运势" or command == "个人运势" or full_text == "个人运势":
            return (False, await self.ai_features.generate_fortune(user_id))

        elif command == "作诗":
            return (False, await self.ai_features.generate_poem(args))

        elif command == "接龙":
            if not args:
                return (True, "请提供一个成语，例如：@BeiXAI 接龙 一马当先")
            return (False, await self.ai_features.idiom_solitaire(args))

        # 默认作为AI对话处理（带用户画像 + 分级上下文）
        full_message = f"{command} {args}" if args else command
        return (False, await self.ai_features.chat_with_profile(group_id, user_id, full_message, user_profile, tier=tier))
