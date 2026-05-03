import hashlib
import random
from datetime import datetime
from typing import List


def generate_daily_seed(user_id: str, date: str) -> int:
    """基于用户ID和日期生成每日种子"""
    seed_str = f"{user_id}_{date}"
    hash_obj = hashlib.md5(seed_str.encode())
    return int(hash_obj.hexdigest(), 16) % (10 ** 8)


def get_random_quote() -> str:
    """获取随机一言"""
    quotes = [
        "生活就像海洋，只有意志坚强的人才能到达彼岸。",
        "成功不是终点，失败也不是末日，继续前进的勇气才最可贵。",
        "每一个不曾起舞的日子，都是对生命的辜负。",
        "世界上只有一种真正的英雄主义，那就是认清生活的真相后依然热爱它。",
        "不要等待机会，而要创造机会。",
        "你的时间有限，不要浪费在重复他人的生活上。",
        "保持饥饿，保持愚蠢。",
        "今天的努力，是为了明天的自己不后悔。"
    ]
    return random.choice(quotes)


def get_zodiac_fortune(zodiac: str) -> str:
    """获取星座运势（简化版）"""
    zodiacs = ["白羊座", "金牛座", "双子座", "巨蟹座", "狮子座", "处女座",
               "天秤座", "天蝎座", "射手座", "摩羯座", "水瓶座", "双鱼座"]

    if zodiac not in zodiacs:
        return f"未找到星座'{zodiac}'，请输入正确的星座名称。"

    fortunes = [
        f"{zodiac}今日运势不错，适合主动出击！",
        f"{zodiac}今天需要保持低调，避免冲突。",
        f"{zodiac}今日财运亨通，可以考虑小额投资。",
        f"{zodiac}今天桃花运旺盛，单身的朋友要把握机会哦！",
        f"{zodiac}今日工作运势佳，适合处理重要事务。"
    ]

    seed = generate_daily_seed(zodiac, datetime.now().strftime("%Y-%m-%d"))
    random.seed(seed)
    return random.choice(fortunes)


def format_message_list(messages: List[tuple]) -> List[str]:
    """格式化消息列表用于总结"""
    formatted = []
    for user_id, message, timestamp in messages:
        time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M")
        formatted.append(f"[{time_str}] {user_id}: {message}")
    return formatted
