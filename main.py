#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键词触发插件 - 免指令前缀触发其他插件功能

功能说明：
1. 监听群聊所有消息
2. 当消息匹配配置的关键词时，自动添加指令前缀
3. 创建新事件派发到 AstrBot 的事件队列，让其他插件能够处理

使用示例：
- 用户发送 "打工" → 创建 "/打工" 命令事件 → 触发打工指令
- 用户发送 "宝宝菜单" → 创建 "/宝宝菜单" 命令事件 → 触发对应插件
"""

from __future__ import annotations

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star

from .core.event_factory import (
    SYNTHETIC_EXTRA_KEY,
    SYNTHETIC_MESSAGE_ID_PREFIX,
    EventFactory,
)


class KeywordTriggerPlugin(Star):
    """关键词触发插件 - 让用户无需输入前缀即可触发指令

    AstrBot v3.5.19 之后会自动识别继承 Star 的类并注册为插件，
    不再需要 @register 装饰器（register_star 已标记废弃）；
    插件的名称、作者、版本等信息统一以 metadata.yaml 为准。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}

        # 解析关键词集合
        self.keywords = self._parse_keywords()

        # 是否仅在群聊中触发
        self.group_only = bool(self.config.get("enable_group_only", True))

        # 创建事件工厂
        self.event_factory = EventFactory(context)

        self.logger.info(
            f"[关键词触发] 插件已加载，共 {len(self.keywords)} 个关键词"
        )
        if self.keywords:
            self.logger.info(f"[关键词触发] 关键词列表: {list(self.keywords)}")

    def _parse_keywords(self) -> set:
        """解析配置中的关键词列表，返回关键词集合"""
        keywords_config = self.config.get("keywords", [])
        result = set()

        for item in keywords_config:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())

        return result

    def _extract_non_text_components(self, event: AstrMessageEvent) -> list:
        """
        从原始消息中提取非文本组件（如 At 组件）
        这确保了真正的 @ 提及不会丢失
        """
        non_text_components = []

        message_chain = getattr(event.message_obj, "message", None)
        if not message_chain:
            return non_text_components

        for comp in message_chain:
            # 保留 At 组件（真正的艾特）
            if isinstance(comp, At):
                non_text_components.append(comp)
                self.logger.debug(f"[关键词触发] 提取到 At 组件: qq={comp.qq}")

        return non_text_components

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，匹配关键词后派发新的命令事件"""
        # 防循环：本插件派发的事件带有 AstrBot 官方 extra 标记
        if event.get_extra(SYNTHETIC_EXTRA_KEY, False):
            self.logger.debug("[关键词触发] 跳过已触发的事件（extra 标记）")
            return

        # 兼容历史版本：合成事件的 message_id 带固定前缀
        message_id = getattr(event.message_obj, "message_id", "") or ""
        if str(message_id).startswith(SYNTHETIC_MESSAGE_ID_PREFIX):
            self.logger.debug(f"[关键词触发] 跳过已触发的事件: {message_id}")
            return

        # 纯文本内容统一取 event.message_str：
        # AstrBot 规定该字段为消息链中所有 Plain 段拼接的结果。
        text = (event.message_str or "").strip()
        if not text:
            return

        # 跳过已经带命令前缀的消息
        if text.startswith("/") or text.startswith("#"):
            self.logger.debug(f"[关键词触发] 跳过命令格式消息: '{text}'")
            return

        # 检查是否仅限群聊（get_group_id 为非群聊时返回空字符串）
        if self.group_only and not event.get_group_id():
            return

        # 检查是否匹配关键词（前缀匹配，支持关键词后带参数）
        matched_keyword = None
        for keyword in self.keywords:
            if text == keyword or text.startswith(keyword):
                # 优先选择更长的关键词匹配（避免短词误匹配）
                if matched_keyword is None or len(keyword) > len(matched_keyword):
                    matched_keyword = keyword

        if not matched_keyword:
            return

        # 获取关键词后面的内容（参数部分）
        suffix = text[len(matched_keyword):]
        new_command = f"/{matched_keyword}{suffix}"

        # 提取原始消息中的非文本组件（如 At 组件）
        original_components = self._extract_non_text_components(event)

        # 获取原始事件的管理员状态
        is_admin = bool(event.is_admin())

        # 获取发送者信息（官方 API，sender 缺失时返回空字符串）
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name() or "用户"

        self.logger.info(
            f"[关键词触发] 匹配关键词 '{matched_keyword}' → 转换为 '{new_command}'"
        )
        if original_components:
            self.logger.info(
                f"[关键词触发] 保留 {len(original_components)} 个非文本组件"
            )

        try:
            # 使用 EventFactory 创建新的命令事件，传递原始消息组件和管理员状态
            new_event = self.event_factory.create_event(
                unified_msg_origin=event.unified_msg_origin,
                command=new_command,
                creator_id=sender_id,
                creator_name=sender_name,
                original_components=original_components,
                is_admin=is_admin,
                original_event=event,
            )

            # 将新事件放入事件队列
            self.context.get_event_queue().put_nowait(new_event)

            self.logger.info(f"[关键词触发] 已派发命令事件: {new_command}")

            # 阻止原消息继续传播，避免 LLM 响应
            event.stop_event()

        except Exception as e:
            self.logger.error(f"[关键词触发] 派发事件失败: {e}")
