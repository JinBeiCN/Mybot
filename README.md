# BeiXAI Bot

QQ 群机器人，基于 [NapCat](https://github.com/NapNeko/NapCatQQ) WebSocket + [SiliconFlow](https://siliconflow.cn) API。

## 功能

### AI 对话
- 群聊 @机器人对话（角色扮演 / `#` 专业模式）
- 私聊贴心朋友模式，含时间情景感知
- 多轮上下文记忆，按用户分级控制轮数
- 长期记忆系统，潜移默化记住用户偏好

### 用户系统
- 三级用户体系：Lv.1 基础 / Lv.2 进阶 / Lv.3 高级
- 每日 AI 配额 + OCR 次数分级控制
- 首次对话偏好向导（称呼 / 兴趣 / 风格）
- 封禁 / 解封管理

### Web 管理面板
- 仪表盘：运行状态、实时速率、打卡配置、模型切换
- 用户管理：封禁 / 分级 / 自定义配额
- 运行日志 + 事件记录
- 用户长期记忆查看

### 其他
- 定时群打卡 + 黄历推送
- 今日发言排行榜（图片生成）
- 群聊总结 + OCR 图片识别
- 主动私聊推送（时间情景化）
- 成语接龙、作诗、运势、星座、笑话
- 消息撤回记录、敏感词过滤、速率限制

## 快速开始

```bash
git clone https://github.com/JinBeiCN/Mybot.git
cd Mybot
pip install -r requirements.txt
cp config.example.json config.json
# 编辑 config.json 填入 NapCat 连接信息和 SiliconFlow API Key
python main.py
# Web 管理面板: http://127.0.0.1:9101
```

## 项目结构

```
mybot/
├── main.py                   # 入口，BeiXAIBot 主类
├── bot/
│   ├── websocket_client.py   # NapCat WebSocket 客户端
│   ├── message_handler.py    # OneBot 消息解析
│   ├── command_router.py     # 命令路由
│   ├── database.py           # SQLite 数据库
│   ├── context_manager.py    # 对话上下文
│   ├── memory.py             # 长期记忆系统
│   └── fortune_cache.py      # 运势缓存
├── features/
│   ├── ai_features.py        # AI 对话/运势/诗词/评论
│   ├── tools.py              # 黄历/笑话
│   └── fun_features.py       # 星座/一言/帮助
├── utils/
│   ├── siliconflow_api.py    # SiliconFlow API 封装
│   └── ranking_generator.py  # 排行榜图片生成
├── web/
│   ├── admin_server.py       # aiohttp Web 服务
│   └── templates/admin.html  # 管理面板前端
└── config.example.json       # 配置文件模板
```

## 作者

**[JinBeiCN](https://github.com/JinBeiCN)**

## License

MIT
