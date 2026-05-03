import json
import re
import html
from typing import Optional, Dict, Any


class MessageHandler:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq

    def parse_message(self, raw_message: str) -> Optional[Dict[str, Any]]:
        """解析OneBot消息"""
        try:
            return json.loads(raw_message)
        except json.JSONDecodeError:
            return None

    def is_group_message(self, message: Dict[str, Any]) -> bool:
        """判断是否为群消息"""
        return message.get("message_type") == "group"

    def is_private_message(self, message: Dict[str, Any]) -> bool:
        """判断是否为私聊消息"""
        return message.get("message_type") == "private"

    def is_mentioned(self, message: Dict[str, Any]) -> bool:
        """判断是否@了机器人"""
        raw_message = message.get("raw_message", "")
        message_content = message.get("message", [])

        # 检查CQ码中的@
        if f"[CQ:at,qq={self.bot_qq}]" in raw_message:
            return True

        # 检查消息数组中的at类型
        if isinstance(message_content, list):
            for segment in message_content:
                if segment.get("type") == "at" and segment.get("data", {}).get("qq") == self.bot_qq:
                    return True

        return False

    def get_reply_message_id(self, message: Dict[str, Any]) -> Optional[str]:
        """获取引用消息的ID"""
        raw_message = message.get("raw_message", "")
        message_content = message.get("message", [])

        # 检查CQ码中的reply
        match = re.search(r'\[CQ:reply,id=(\d+)\]', raw_message)
        if match:
            return match.group(1)

        # 检查消息数组中的reply类型
        if isinstance(message_content, list):
            for segment in message_content:
                if segment.get("type") == "reply":
                    reply_data = segment.get("data", {})
                    msg_id = reply_data.get("id")
                    if msg_id:
                        return str(msg_id)

        return None

    def get_mentioned_users(self, message: Dict[str, Any]) -> list:
        """获取消息中被@的所有用户QQ列表"""
        raw_message = message.get("raw_message", "")
        message_content = message.get("message", [])
        mentioned = []

        # 从 raw_message 中提取所有 @CQ码
        at_matches = re.findall(r'\[CQ:at,qq=(\d+)\]', raw_message)
        mentioned.extend(at_matches)

        # 从 message 数组中提取 at 类型
        if isinstance(message_content, list):
            for segment in message_content:
                if segment.get("type") == "at":
                    qq = segment.get("data", {}).get("qq")
                    if qq and qq not in mentioned:
                        mentioned.append(str(qq))

        return mentioned

    def extract_command(self, message: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """提取命令和参数"""
        raw_message = message.get("raw_message", "")

        # 移除@机器人的部分
        text = raw_message.replace(f"[CQ:at,qq={self.bot_qq}]", "").strip()

        if not text:
            return None, None

        parts = text.split(maxsplit=1)
        command = parts[0]
        args = parts[1] if len(parts) > 1 else None

        return command, args

    def get_group_id(self, message: Dict[str, Any]) -> Optional[str]:
        """获取群号"""
        return str(message.get("group_id")) if message.get("group_id") else None

    def get_user_id(self, message: Dict[str, Any]) -> Optional[str]:
        """获取用户QQ号"""
        return str(message.get("user_id")) if message.get("user_id") else None

    def get_nickname(self, message: Dict[str, Any]) -> str:
        """获取发送者昵称"""
        sender = message.get("sender", {})
        nickname = sender.get("nickname", "")
        if not nickname:
            # 尝试从消息中直接获取
            nickname = str(message.get("user_id", ""))
        return nickname

    def get_plain_text(self, message: Dict[str, Any]) -> str:
        """获取消息内容，保留表情/图片/语音等，移除@"""
        raw_message = message.get("raw_message", "")

        import re

        # 定义CQ码替换规则
        cq_replacements = {
            r'\[CQ:at,qq=all\]': '[全体成员]',
            r'\[CQ:at,qq=(\d+)\]': r'[@\1]',
            r'\[CQ:face,id=(\d+)\]': r'[表情\1]',
            r'\[CQ:image,file=[^\]]+\]': '[图片]',
            r'\[CQ:record,file=[^\]]+\]': '[语音]',
            r'\[CQ:video,file=[^\]]+\]': '[视频]',
            r'\[CQ:rich,key=[^\]]+,preview=[^\]]+,summary=[^\]]+\]': '[卡片消息]',
            r'\[CQ:json,data=[^\]]+\]': '[JSON消息]',
            r'\[CQ:xml,data=[^\]]+\]': '[XML消息]',
            r'\[CQ:marketface,id=(\d+)\]': r'[表情包\1]',
            r'\[CQ:miniapp,appid=[^\]]+,title=[^\]]+\]': '[小程序]',
        }

        # 逐个替换
        result = raw_message
        for pattern, replacement in cq_replacements.items():
            result = re.sub(pattern, replacement, result)

        # 移除剩余的 CQ 码但保留内容
        result = re.sub(r'\[CQ:([^\]]+)\]', r'[\1]', result)

        return result.strip()

    def extract_media_info(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """提取消息中的媒体信息（图片等）"""
        raw_message = message.get("raw_message", "")
        message_content = message.get("message", [])
        images = []

        # 从结构化消息中提取图片
        if isinstance(message_content, list):
            for segment in message_content:
                if segment.get("type") == "image":
                    data = segment.get("data", {})
                    images.append({
                        "file": data.get("file", ""),
                        "url": html.unescape(data.get("url", "")),
                        "sub_type": data.get("subType") or data.get("sub_type", ""),
                    })

        # 从 raw_message 兜底提取图片 CQ 码
        if not images:
            image_matches = re.findall(r'\[CQ:image,([^\]]+)\]', raw_message)
            for match in image_matches:
                item = {"file": "", "url": "", "sub_type": ""}
                for part in match.split(','):
                    if '=' in part:
                        key, value = part.split('=', 1)
                        if key == 'file':
                            item['file'] = value
                        elif key == 'url':
                            item['url'] = html.unescape(value)
                        elif key in ('subType', 'sub_type'):
                            item['sub_type'] = value
                images.append(item)

        return {"images": images}

    def detect_loose_command(self, message: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """宽松命令检测：识别文本形式中包含的bot名称/QQ和运势命令
        匹配场景：
        - 复制文本 "@3908562632 运势"
        - "运势" 或 "个人运势"（仅在群消息中）
        返回: (command, args) 或 (None, None)
        """
        raw_message = message.get("raw_message", "")
        plain_text = self.get_plain_text(message)

        # 检查是否包含 bot QQ 号（复制文本形式）
        if self.bot_qq and f"@{self.bot_qq}" in raw_message:
            # 移除 @QQ 部分后提取命令
            text = raw_message.replace(f"@{self.bot_qq}", "").strip()
            if text:
                parts = text.split(maxsplit=1)
                cmd = parts[0]
                arg = parts[1] if len(parts) > 1 else None
                if cmd in ("运势", "个人运势"):
                    return cmd, arg

        # 检查是否是纯运势命令（需要是群消息且较短命令格式）
        if plain_text in ("运势", "个人运势"):
            return plain_text, None

        return None, None

    def is_group_decrease_event(self, message: Dict[str, Any]) -> bool:
        """判断是否为群成员退出事件"""
        return (
            message.get("post_type") == "notice" and
            message.get("notice_type") == "group_decrease"
        )

    def get_leave_info(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """获取退群信息"""
        if not self.is_group_decrease_event(message):
            return None

        return {
            "group_id": str(message.get("group_id", "")),
            "user_id": str(message.get("user_id", "")),
            "sub_type": message.get("sub_type", ""),  # leave/kick_me/kick
        }

    def is_group_recall_event(self, message: Dict[str, Any]) -> bool:
        """判断是否为群消息撤回事件"""
        return (
            message.get("post_type") == "notice" and
            message.get("notice_type") == "group_recall"
        )

    def get_recall_info(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """获取撤回信息"""
        if not self.is_group_recall_event(message):
            return None

        return {
            "group_id": str(message.get("group_id", "")),
            "user_id": str(message.get("user_id", "")),  # 撤回消息的人
            "message_id": str(message.get("message_id", "")),  # 被撤回的消息ID
        }
