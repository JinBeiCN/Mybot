from utils.helpers import get_random_quote, get_zodiac_fortune


class FunFeatures:
    def __init__(self):
        pass

    def get_daily_quote(self) -> str:
        """获取每日一言"""
        quote = get_random_quote()
        return f"💭 每日一言\n\n{quote}"

    def get_zodiac_fortune(self, zodiac: str) -> str:
        """获取星座运势"""
        fortune = get_zodiac_fortune(zodiac)
        return f"✨ 星座运势\n\n{fortune}"

    def get_help(self) -> str:
        """获取帮助信息"""
        help_text = """🤖 BeiXAI 命令列表

💬 AI对话：
• @我说话 - 攻略模式（角色扮演）
• #问题 - 专业AI模式
• 清除上下文 - 清除对话历史
• 信息 - 查看机器人版本信息

📊 AI功能：
• 总结 - 总结最近的群聊记录
• 运势 - 查看今日运势
• 作诗 [主题] - 生成诗词
• 接龙 [成语] - 成语接龙

🛠️ 工具功能：
• 黄历 - 查看今日黄历
• 笑话 - 讲个笑话

🎮 娱乐功能：
• 星座 [星座名] - 查询星座运势
• 一言 - 每日一言

📈 自动触发（无需@）：
• 逼话榜/说话榜 - 生成今日发言排行榜

💡 提示：
• 普通聊天 = 攻略模式
• #问题 = 专业AI模式"""
        return help_text
