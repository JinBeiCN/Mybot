from datetime import datetime
import random
from typing import Optional


class Tools:
    def get_huangli(self) -> str:
        """获取黄历信息（简化版）"""
        date = datetime.now().strftime("%Y年%m月%d日")
        weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][datetime.now().weekday()]

        yi = ["出行", "嫁娶", "搬家", "开业", "祈福", "求财"]
        ji = ["动土", "破土", "安葬", "修造", "诉讼", "词讼"]

        random.seed(datetime.now().strftime("%Y%m%d"))
        selected_yi = random.sample(yi, 3)
        selected_ji = random.sample(ji, 3)

        result = f"📅 今日黄历\n\n"
        result += f"日期: {date} {weekday}\n\n"
        result += f"宜: {' '.join(selected_yi)}\n"
        result += f"忌: {' '.join(selected_ji)}\n"

        return result

    def get_joke(self) -> str:
        """获取笑话（简化版）"""
        jokes = [
            "为什么程序员总是分不清万圣节和圣诞节？\n因为 Oct 31 == Dec 25",
            "一个程序员去面试，面试官问：你有女朋友吗？\n程序员：有啊。\n面试官：那你能给我看看吗？\n程序员：可以啊。\n然后打开了GitHub...",
            "为什么程序员喜欢用暗色主题？\n因为光吸引bug。",
            "程序员的三大谎言：\n1. 这个bug我马上就能修好\n2. 这段代码不需要注释\n3. 我一定会写文档的",
            "老板：这个需求很简单，你一天能做完吗？\n程序员：可以。\n（三天后）\n老板：怎么还没做完？\n程序员：需求变了三次..."
        ]

        return f"😄 笑话时间\n\n{random.choice(jokes)}"
