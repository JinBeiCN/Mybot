import asyncio
import websockets
import json
import logging
import os
import uuid
from typing import Callable, Optional, Dict


class WebSocketClient:
    def __init__(self, host: str, port: int, token: str, heartbeat_interval: int = 60):
        self.uri = f"ws://{host}:{port}"
        self.token = token
        self.heartbeat_interval = heartbeat_interval
        self.websocket = None
        self.running = False
        self.message_handler: Optional[Callable] = None
        self.logger = logging.getLogger("WebSocketClient")
        # 待处理的 get_group_list 响应（没有 echo）
        self._pending_group_list: Optional[dict] = None
        # 基于 echo 的独立群打卡响应 {echo: (event, result_dict)}
        self._pending_sign_responses: Dict[str, tuple] = {}

    def set_message_handler(self, handler: Callable):
        """设置消息处理回调"""
        self.message_handler = handler

    async def connect(self):
        """连接到WebSocket服务器"""
        try:
            self.websocket = await websockets.connect(self.uri)
            self.logger.info(f"已连接到 {self.uri}")
            return True
        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            return False

    async def send_message(self, message: dict):
        """发送消息"""
        try:
            if self.websocket:
                await self.websocket.send(json.dumps(message))
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")

    async def send_group_message(self, group_id: str, message: str):
        """发送群消息"""
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": int(group_id),
                "message": message
            }
        }
        await self.send_message(payload)

    async def send_private_message(self, user_id: str, message: str):
        """发送私聊消息"""
        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": int(user_id),
                "message": message
            }
        }
        await self.send_message(payload)

    async def send_group_image(self, group_id: str, image_data: bytes):
        """发送群图片"""
        temp_path = f"data/temp_{uuid.uuid4().hex}.png"
        os.makedirs("data", exist_ok=True)
        with open(temp_path, 'wb') as f:
            f.write(image_data)

        try:
            payload = {
                "action": "send_group_msg",
                "params": {
                    "group_id": int(group_id),
                    "message": f"[CQ:image,file=file:///{os.path.abspath(temp_path)}]"
                }
            }
            await self.send_message(payload)
            # 延长清理时间，让NapCat有足够时间处理大图
            asyncio.get_running_loop().call_later(30, self._cleanup_temp_file, temp_path)
        except Exception:
            self._cleanup_temp_file(temp_path)
            raise

    async def send_private_image(self, user_id: str, image_data: bytes):
        """发送私聊图片"""
        temp_path = f"data/temp_{uuid.uuid4().hex}.png"
        os.makedirs("data", exist_ok=True)
        with open(temp_path, 'wb') as f:
            f.write(image_data)
        try:
            payload = {
                "action": "send_private_msg",
                "params": {
                    "user_id": int(user_id),
                    "message": f"[CQ:image,file=file:///{os.path.abspath(temp_path)}]"
                }
            }
            await self.send_message(payload)
            asyncio.get_running_loop().call_later(30, self._cleanup_temp_file, temp_path)
        except Exception:
            self._cleanup_temp_file(temp_path)
            raise

    def _cleanup_temp_file(self, temp_path: str):
        """清理临时文件"""
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass

    async def heartbeat_loop(self):
        """心跳循环"""
        while self.running:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                if self.websocket:
                    heartbeat_msg = {"action": "get_status"}
                    await self.send_message(heartbeat_msg)
            except Exception as e:
                self.logger.warning(f"心跳失败: {e}")
                await asyncio.sleep(5)

    async def receive_loop(self):
        """接收消息循环"""
        while self.running:
            try:
                if self.websocket:
                    message = await self.websocket.recv()

                    # 忽略空消息或非字符串
                    if not message or not isinstance(message, (str, bytes)):
                        continue

                    # 解析消息
                    parsed = None
                    try:
                        parsed = json.loads(message)
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # 安全检查：parsed 必须是 dict 类型
                    if not isinstance(parsed, dict):
                        continue

                    # 检查 retcode 是否为 0（成功）
                    retcode = parsed.get("retcode")
                    if retcode == 0:
                        # 如果有待处理的 get_group_list 请求（需要 data 不为 null）
                        data = parsed.get("data")
                        if self._pending_group_list is not None and data is not None:
                            self._pending_group_list["data"] = data
                            self._pending_group_list["event"].set()

                    # 检查 echo 响应（基于独立 key，支持并发）
                    echo = parsed.get("echo")
                    if echo and self._pending_sign_responses and echo in self._pending_sign_responses:
                        event, result = self._pending_sign_responses.pop(echo)
                        result["success"] = (retcode == 0)
                        event.set()

                    # 调用消息处理器
                    if self.message_handler:
                        try:
                            await self.message_handler(message)
                        except Exception as e:
                            self.logger.error(f"消息处理异常: {e}")

            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("连接断开，尝试重连...")
                await asyncio.sleep(5)
                if await self.connect():
                    self.logger.info("重连成功")
            except asyncio.CancelledError:
                self.logger.info("接收循环被取消")
                break
            except Exception as e:
                self.logger.error(f"接收消息失败: {e}")
                await asyncio.sleep(1)

    async def start(self):
        """启动客户端"""
        self.running = True
        if not await self.connect():
            self.logger.error("初始连接失败")
            return

        try:
            await asyncio.gather(
                self.heartbeat_loop(),
                self.receive_loop()
            )
        except Exception as e:
            self.logger.error(f"运行错误: {e}")

    async def stop(self):
        """停止客户端"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
        self.logger.info("WebSocket客户端已停止")

    async def send_group_sign(self, group_id: str) -> bool:
        """群打卡，基于 echo 独立匹配响应，支持并发"""
        echo = f"sign_{group_id}_{uuid.uuid4().hex}"
        payload = {
            "action": "send_group_sign",
            "params": {
                "group_id": int(group_id)
            },
            "echo": echo
        }

        response_event = asyncio.Event()
        result = {"success": False}
        self._pending_sign_responses[echo] = (response_event, result)

        try:
            await self.send_message(payload)
            self.logger.info(f"已发送群打卡请求 (群:{group_id})")
            await asyncio.wait_for(response_event.wait(), timeout=10)
            return result.get("success", False)
        except asyncio.TimeoutError:
            self.logger.warning(f"群 {group_id} 打卡响应超时，视为成功")
            return True
        except Exception as e:
            self.logger.error(f"群 {group_id} 打卡失败: {e}")
            return False
        finally:
            self._pending_sign_responses.pop(echo, None)

    async def get_group_list(self) -> list:
        """获取群列表"""
        payload = {
            "action": "get_group_list",
            "params": {}
        }

        response_event = asyncio.Event()
        result = {"data": [], "event": response_event}
        self._pending_group_list = result

        try:
            await self.send_message(payload)
            # 等待响应，最多5秒
            await asyncio.wait_for(response_event.wait(), timeout=5)
            groups = result.get("data", [])
            self.logger.info(f"获取群列表成功，共 {len(groups)} 个群")
            return groups
        except asyncio.TimeoutError:
            self.logger.warning("获取群列表超时")
            return []
        except Exception as e:
            self.logger.error(f"获取群列表失败: {e}")
            return []
        finally:
            self._pending_group_list = None
