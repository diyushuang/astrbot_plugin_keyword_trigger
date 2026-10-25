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

from astrbot.api import logger
from astrbot.api.star import Star, Context, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, At

from .core.event_factory import EventFactory


@register("keyword_trigger", "haoyuedashi", "免指令前缀关键词触发插件", "1.0.0")
class KeywordTriggerPlugin(Star):
    """关键词触发插件 - 让用户无需输入前缀即可触发指令"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 解析关键词集合
        self.keywords = self._parse_keywords()
        
        # 是否仅在群聊中触发
        self.group_only = self.config.get("enable_group_only", True)
        
        # 创建事件工厂
        self.event_factory = EventFactory(context)
        
        # 记录已触发的事件 ID，防止重复处理
        self.triggered_event_ids = set()
        
        logger.info(f"[关键词触发] 插件已加载，共 {len(self.keywords)} 个关键词")
        if self.keywords:
            logger.info(f"[关键词触发] 关键词列表: {list(self.keywords)}")

    def _parse_keywords(self) -> set:
        """解析配置中的关键词列表，返回关键词集合"""
        keywords_config = self.config.get("keywords", [])
        result = set()
        
        for item in keywords_config:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())
                    
        return result

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        """获取群组ID"""
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'group_id'):
            return str(event.message_obj.group_id) if event.message_obj.group_id else ""
        return ""

    def _get_message_id(self, event: AstrMessageEvent) -> str:
        """获取消息ID"""
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message_id'):
            return str(event.message_obj.message_id) if event.message_obj.message_id else ""
        return ""

    def _get_plain_text(self, event: AstrMessageEvent) -> str:
        """从消息中提取纯文本内容
        
        优先从消息组件链中提取 Plain 文本，回退到 message_str。
        """
        try:
            if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
                message_chain = event.message_obj.message
                if message_chain:
                    for comp in message_chain:
                        if isinstance(comp, Plain) and hasattr(comp, 'text'):
                            return comp.text.strip()
        except (AttributeError, TypeError):
            pass
        
        return event.message_str.strip() if event.message_str else ""

    def _extract_non_text_components(self, event: AstrMessageEvent) -> list:
        """
        从原始消息中提取非文本组件（如 At 组件）
        这确保了真正的 @ 提及不会丢失
        """
        non_text_components = []
        
        try:
            if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
                message_chain = event.message_obj.message
                if message_chain:
                    for comp in message_chain:
                        # 保留 At 组件（真正的艾特）
                        if isinstance(comp, At):
                            non_text_components.append(comp)
                            logger.debug(f"[关键词触发] 提取到 At 组件: qq={comp.qq}")
        except (AttributeError, TypeError) as e:
            logger.warning(f"[关键词触发] 提取消息组件时出错: {e}")
        
        return non_text_components

    @filter.event_message_type(filter.EventMessageType.ALL)  
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，匹配关键词后派发新的命令事件"""
        # 检查是否是我们自己触发的事件（克隆标记 / message_id 前缀双保险）
        _orig_msg = getattr(event, "message_obj", None)
        if getattr(_orig_msg, "command_trigger_synthetic", False):
            logger.debug("[关键词触发] 跳过已触发的事件（synthetic 标记）")
            return
        message_id = self._get_message_id(event)
        if message_id.startswith("command_trigger_"):
            logger.debug(f"[关键词触发] 跳过已触发的事件: {message_id}")
            return
        
        # 提取纯文本内容
        text = self._get_plain_text(event)
        if not text:
            return
        
        # 跳过已经带命令前缀的消息
        if text.startswith("/") or text.startswith("#"):
            logger.debug(f"[关键词触发] 跳过命令格式消息: '{text}'")
            return
        
        # 检查是否仅限群聊
        if self.group_only:
            group_id = self._get_group_id(event)
            if not group_id:
                return
        
        # 检查是否匹配关键词（前缀匹配，支持关键词后带参数）
        matched_keyword = None
        for keyword in self.keywords:
            if text == keyword or text.startswith(keyword):
                # 优先选择更长的关键词匹配（避免短词误匹配）
                if matched_keyword is None or len(keyword) > len(matched_keyword):
                    matched_keyword = keyword
        
        if matched_keyword:
            # 获取关键词后面的内容（参数部分）
            suffix = text[len(matched_keyword):]
            new_command = f"/{matched_keyword}{suffix}"
            
            # 提取原始消息中的非文本组件（如 At 组件）
            original_components = self._extract_non_text_components(event)
            
            # 获取原始事件的管理员状态
            is_admin = False
            try:
                is_admin = event.is_admin()
            except (AttributeError, TypeError):
                pass
            
            logger.info(f"[关键词触发] 匹配关键词 '{matched_keyword}' → 转换为 '{new_command}'")
            if original_components:
                logger.info(f"[关键词触发] 保留 {len(original_components)} 个非文本组件")
            
            try:
                # 获取发送者信息
                sender_id = str(event.get_sender_id())
                sender_name = event.get_sender_name() if hasattr(event, 'get_sender_name') else "用户"
                
                # 使用 EventFactory 创建新的命令事件，传递原始消息组件和管理员状态
                new_event = self.event_factory.create_event(
                    unified_msg_origin=event.unified_msg_origin,
                    command=new_command,
                    creator_id=sender_id,
                    creator_name=sender_name,
                    original_components=original_components,
                    is_admin=is_admin,
                    original_event=event
                )
                
                # 将新事件放入事件队列
                self.context.get_event_queue().put_nowait(new_event)
                
                logger.info(f"[关键词触发] 已派发命令事件: {new_command}")
                
                # 阻止原消息继续传播，避免 LLM 响应
                event.stop_event()
                
            except Exception as e:
                logger.error(f"[关键词触发] 派发事件失败: {e}")
