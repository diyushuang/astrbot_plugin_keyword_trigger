# 🔑 免前缀触发指令插件

> 群内免指令前缀触发其他插件功能的 AstrBot 插件。

![version](https://img.shields.io/badge/version-1.0.2-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0%2C%3C5-orange)

## ✨ 功能介绍

让用户无需输入 `/` 或 `#` 前缀即可触发 AstrBot 的命令，大幅降低使用门槛。

### 工作原理

```
用户发送消息 → 关键词匹配 → 自动添加 / 前缀 → 克隆原始消息构造命令事件 → 派发到事件队列 → 触发目标插件
```

### 核心特性

- 🎯 **前缀匹配**：支持关键词后携带参数（如 `购买宝宝 @群友`）
- 🔄 **智能去重**：自动跳过已带 `/` 或 `#` 前缀的消息，避免重复处理
- 🛡️ **防止递归**：使用 AstrBot 官方 `event.set_extra()` 标记合成事件，并以 `message_id` 前缀做兼容兜底
- 📎 **组件保留**：自动提取并保留原始消息中的 `@` 提及等非文本组件
- 🧬 **真实消息源**：克隆原始 `AstrBotMessage`，保留真实 `raw_message` / `message_id`，满足平台被动回复要求
- 🌐 **多平台适配**：支持 aiocqhttp（NapCat / OneBot v11）、QQ 官方（含 Webhook）、Telegram、Discord、Slack、飞书、钉钉、WebChat
- ⚡ **事件阻断**：匹配成功后自动阻止原消息继续传播，避免 LLM 误响应

## 📖 使用示例

| 用户输入 | 插件行为 | 触发命令 |
|---------|---------|---------|
| `打工` | ✅ 完全匹配 | `/打工` |
| `宝宝菜单` | ✅ 完全匹配 | `/宝宝菜单` |
| `购买宝宝 @群友` | ✅ 前缀匹配（保留 @ 组件） | `/购买宝宝 @群友` |
| `/打工` | ❌ 跳过 | 直接由 AstrBot 处理 |
| `#打工` | ❌ 跳过 | 直接由 AstrBot 处理 |
| `去打工` | ❌ 不匹配 | 无 |

## ⚙️ 配置说明

在 AstrBot 后台配置插件参数：

### keywords（关键词列表）

配置需要触发的关键词，支持多个。当存在多个关键词可匹配时，插件会**优先匹配最长的关键词**，避免短词误触发。

**示例配置：**
```json
["打工", "宝宝菜单", "踩雷", "签到", "购买宝宝"]
```

### enable_group_only（仅群聊触发）

- `true`（默认）：只在群聊中触发
- `false`：私聊也可触发

## 🏗️ 项目结构

```
astrbot_plugin_keyword_trigger/
├── main.py              # 插件主入口，消息监听与关键词匹配
├── core/
│   └── event_factory.py # 事件工厂，按平台类型创建正确的事件对象
├── _conf_schema.json    # 插件配置定义
├── metadata.yaml        # 插件元数据（名称/版本/支持平台）
├── requirements.txt     # 依赖声明（本插件无第三方依赖）
├── .gitignore
└── README.md
```

## 🔌 平台接入要点

### aiocqhttp / NapCat（OneBot v11）

NapCat 的网络配置中 `messagePostFormat` 必须为 `array`，AstrBot 的 aiocqhttp 适配器按消息段数组解析上报消息；若为 `string` 将无法正确解析消息链。

```json5
{
  "network": {
    "websocketServers": [
      {
        "name": "WsServer",
        "enable": true,
        "host": "0.0.0.0",
        "port": 3001,
        "messagePostFormat": "array", // 必须为 array
        "reportSelfMessage": false,
        "token": ""
      }
    ]
  }
}
```

### QQ 官方机器人（qq_official / qq_official_webhook）

插件通过克隆原始消息对象保留真实的 `message_id` 与 `raw_message`，从而走通被动回复通道。按 QQ 官方 API v2 规定，被动回复存在以下限制：

- `msg_id` 自收到消息起 **5 分钟内**有效（群聊）；
- 同一条消息最多回复 **5 次**；
- 相同的 `msg_id + msg_seq` 组合重复发送会失败。

因此目标插件若在触发后延迟较久才回复，可能因超出被动回复窗口而发送失败——这属于平台侧限制，不是插件缺陷。

## ⚠️ 注意事项

1. **前缀匹配**：用户消息以关键词开头即可触发，支持后接参数
2. **最长匹配优先**：配置 `购买` 和 `购买宝宝` 时，输入 `购买宝宝` 会匹配更长的关键词
3. **不重复触发**：已经带 `/` 或 `#` 前缀的消息不会被处理
4. **事件阻断**：匹配成功后原消息会被阻断，不会传递给 LLM 或其他插件
5. **文本来源**：匹配使用 `event.message_str`（AstrBot 定义为消息链中所有 Plain 段拼接结果）

## 📦 安装

将插件文件夹放入 AstrBot 的 `data/plugins/` 目录，重启 AstrBot 即可。

要求 AstrBot `>=4.0,<5`（`metadata.yaml` 中 `astrbot_version` 声明，不满足时框架会阻止加载）。

## 👤 作者

haoyuedashi
