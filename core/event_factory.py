import copy
import time
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.api.message_components import Plain
from astrbot.api.platform import MessageMember


class EventFactory:
    """事件工厂类，用于创建不同平台类型的事件对象"""

    def __init__(self, context: Context):
        self.context = context

    def _get_platform_instance(self, platform_id: str):
        """获取平台实例，通过平台ID（用户自定义名称）获取"""
        try:
            platform_inst = self.context.get_platform_inst(platform_id)
            if platform_inst:
                logger.debug(f"成功通过平台ID获取平台实例: {platform_id}")
                return platform_inst
        except Exception as e:
            logger.warning(f"通过context.get_platform_inst获取平台实例失败: {e}")

        logger.error(f"无法获取平台实例: {platform_id}")
        return None

    def create_event(
        self,
        unified_msg_origin: str,
        command: str,
        creator_id: str,
        creator_name: str = None,
        original_components: list = None,
        is_admin: bool = False,
        original_event: AstrMessageEvent | None = None,
    ) -> AstrMessageEvent:
        """创建事件对象，根据平台类型自动选择正确的事件类
        
        Args:
            unified_msg_origin: 统一消息来源
            command: 命令文本
            creator_id: 创建者ID
            creator_name: 创建者名称
            original_components: 原始消息中的非文本组件（如 At 组件）
            is_admin: 原始事件发送者是否为管理员
        """

        # 解析平台信息
        platform_id = "unknown"
        session_id = "unknown"
        message_type = MessageType.FRIEND_MESSAGE

        if ":" in unified_msg_origin:
            parts = unified_msg_origin.split(":")
            if len(parts) >= 3:
                platform_id = parts[0]
                msg_type_str = parts[1]
                session_id = ":".join(parts[2:])

                if "GroupMessage" in msg_type_str:
                    message_type = MessageType.GROUP_MESSAGE
                elif "FriendMessage" in msg_type_str:
                    message_type = MessageType.FRIEND_MESSAGE

        logger.debug(f"create_event: platform_id={platform_id}, unified_msg_origin={unified_msg_origin}")

        # 获取平台实例
        platform_instance = self._get_platform_instance(platform_id)

        # 获取真实的平台类型
        platform_type = self._get_platform_type_from_instance(platform_instance, unified_msg_origin)
        logger.debug(f"create_event: 获取到的平台类型={platform_type}")

        # 优先克隆原始事件的消息对象：保留真实 raw_message / message_id /
        # sender 等。qq_official 发送路径要求 raw_message 是 botpy 的
        # GroupMessage/C2CMessage 真实消息源，且被动回复 msg_id 必须是
        # 收到的真实消息 ID（官方限制：5 分钟内有效，每条最多回 5 次）。
        msg = None
        original_msg = getattr(original_event, "message_obj", None)
        if original_event is not None and original_msg is not None:
            try:
                msg = self._clone_message_object(original_msg, command, original_components)
                logger.debug("create_event: 已克隆原始事件消息对象")
            except Exception as e:
                logger.warning(f"克隆原始消息对象失败，回退到合成消息: {e}")

        if msg is None:
            # 创建基础消息对象，传递原始组件
            msg = self._create_message_object(
                command, session_id, message_type, creator_id,
                creator_name, platform_instance, original_components,
            )

        # 创建平台元数据
        meta = PlatformMetadata(platform_type, "command_trigger", platform_id)

        # 根据平台类型创建正确的事件对象
        event = self._create_platform_specific_event(
            platform_type, command, msg, meta, session_id, platform_instance,
        )

        # 继承原始事件的管理员状态，而非硬编码
        _is_admin = is_admin
        event.is_admin = lambda: _is_admin
        event.get_sender_id = lambda: creator_id
        event.get_sender_name = lambda: creator_name or "用户"
        
        return event

    def _get_platform_type_from_instance(self, platform_instance, unified_msg_origin: str) -> str:
        """优先从平台实例获取类型，否则从origin解析"""
        if platform_instance:
            try:
                if hasattr(platform_instance, 'meta'):
                    meta = platform_instance.meta()
                    if hasattr(meta, 'name'):
                        return meta.name
            except (AttributeError, TypeError) as e:
                logger.warning(f"从平台实例获取类型失败: {e}")

        return self._get_platform_type_from_origin(unified_msg_origin)

    def _get_platform_type_from_origin(self, unified_msg_origin: str) -> str:
        """从unified_msg_origin中获取平台类型"""
        if ":" in unified_msg_origin:
            platform_part = unified_msg_origin.split(":")[0]
            platform_mapping = {
                "aiocqhttp": "aiocqhttp",
                "qq_official": "qq_official",
                "telegram": "telegram",
                "discord": "discord",
                "slack": "slack",
                "lark": "lark",
                "wechatpadpro": "wechatpadpro",
                "webchat": "webchat",
                "dingtalk": "dingtalk",
            }
            return platform_mapping.get(platform_part, platform_part)
        return "unknown"

    def _get_real_self_id(self, platform_instance) -> str:
        """获取真实的机器人ID，无法获取时返回空字符串"""
        if not platform_instance:
            return ""

        try:
            if hasattr(platform_instance, 'client_self_id'):
                val = getattr(platform_instance, 'client_self_id')
                if val and isinstance(val, (str, int)):
                    return str(val)
        except (AttributeError, TypeError) as e:
            logger.debug(f"获取机器人ID时发生异常: {e}")

        return ""

    def _clone_message_object(
        self,
        original_msg: AstrBotMessage,
        command: str,
        original_components: list = None,
    ) -> AstrBotMessage:
        """基于原始消息对象克隆新事件的消息对象。

        仅替换 message_str 与消息链（命令文本 + 原始非文本组件），
        raw_message、message_id、sender、session_id、group_id、self_id
        等全部沿用原始消息，保证平台事件发送路径能拿到真实消息源。
        """
        msg = copy.copy(original_msg)
        msg.message_str = command
        message_chain = [Plain(command)]
        if original_components:
            message_chain.extend(original_components)
        msg.message = message_chain
        # 防循环标记：on_message 据此跳过自身派发的事件
        msg.command_trigger_synthetic = True
        return msg

    def _create_message_object(
        self,
        command: str,
        session_id: str,
        message_type: MessageType,
        creator_id: str,
        creator_name: str = None,
        platform_instance=None,
        original_components: list = None,
    ) -> AstrBotMessage:
        """创建消息对象
        
        Args:
            command: 命令文本
            session_id: 会话ID
            message_type: 消息类型
            creator_id: 创建者ID
            creator_name: 创建者名称
            platform_instance: 平台实例
            original_components: 原始消息中的非文本组件（如 At 组件）
        """
        msg = AstrBotMessage()
        msg.message_str = command
        msg.session_id = session_id
        msg.type = message_type

        # 获取真实的机器人ID
        real_self_id = self._get_real_self_id(platform_instance)
        msg.self_id = real_self_id
        if real_self_id:
            logger.debug(f"使用机器人ID: {msg.self_id}")
        else:
            logger.debug("未能获取机器人ID，使用空字符串")

        msg.message_id = "command_trigger_" + str(int(time.time()))

        # 设置发送者信息
        msg.sender = MessageMember(user_id=creator_id, nickname=creator_name or "用户")

        # 设置群组ID（如果是群聊）
        if message_type == MessageType.GROUP_MESSAGE:
            if "_" in session_id:
                group_id = session_id.split("_")[0]
            else:
                group_id = session_id
            msg.group_id = group_id

        # 构建消息链：先放命令文本，再放原始非文本组件（如 At）
        message_chain = [Plain(command)]
        if original_components:
            message_chain.extend(original_components)
            logger.debug(f"消息链中添加了 {len(original_components)} 个原始组件")
        msg.message = message_chain

        # 设置raw_message属性
        msg.raw_message = {
            "message": command,
            "message_type": "group" if message_type == MessageType.GROUP_MESSAGE else "private",
            "sender": {"user_id": creator_id, "nickname": creator_name or "用户"},
            "self_id": msg.self_id,
        }
        
        if message_type == MessageType.GROUP_MESSAGE:
            msg.raw_message["group_id"] = msg.group_id

        return msg

    def _create_platform_specific_event(
        self,
        platform_name: str,
        command: str,
        msg: AstrBotMessage,
        meta: PlatformMetadata,
        session_id: str,
        platform_instance=None,
    ) -> AstrMessageEvent:
        """根据平台类型创建特定的事件对象
        
        尝试动态导入对应平台的事件类，失败时回退到基础事件。
        """

        # 平台事件类映射：(模块路径, 类名, 平台实例属性名)
        platform_event_map = {
            "aiocqhttp": (
                "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event",
                "AiocqhttpMessageEvent",
                "bot",
            ),
            "qq_official": (
                "astrbot.core.platform.sources.qqofficial.qqofficial_message_event",
                "QQOfficialMessageEvent",
                "client",  # 平台实例属性名（adapter 的 botClient 在 self.client）
                "bot",     # 事件构造参数名：QQOfficialMessageEvent(bot=...)
            ),
            "telegram": (
                "astrbot.core.platform.sources.telegram.tg_event",
                "TelegramPlatformEvent",
                "client",
            ),
            "discord": (
                "astrbot.core.platform.sources.discord.discord_platform_event",
                "DiscordPlatformEvent",
                "client",
            ),
            "slack": (
                "astrbot.core.platform.sources.slack.slack_event",
                "SlackMessageEvent",
                "web_client",
            ),
            "lark": (
                "astrbot.core.platform.sources.lark.lark_event",
                "LarkMessageEvent",
                "bot",
            ),
            "wechatpadpro": (
                "astrbot.core.platform.sources.wechatpadpro.wechatpadpro_message_event",
                "WeChatPadProMessageEvent",
                None,  # 使用 adapter=platform
            ),
            "dingtalk": (
                "astrbot.core.platform.sources.dingtalk.dingtalk_event",
                "DingtalkMessageEvent",
                "client",
            ),
        }

        # WebChat 特殊处理：不需要平台实例
        if platform_name == "webchat":
            return self._create_webchat_event(command, msg, meta, session_id)

        if platform_name in platform_event_map:
            module_path, class_name, attr_name, *rest = platform_event_map[platform_name]
            kwarg_name = rest[0] if rest else None
            event = self._try_create_platform_event(
                platform_name, module_path, class_name, attr_name,
                command, msg, meta, session_id, kwarg_name=kwarg_name,
            )
            if event:
                return event

        # 默认回退到基础事件
        return self._create_base_event(command, msg, meta, session_id)

    def _try_create_platform_event(
        self,
        platform_name: str,
        module_path: str,
        class_name: str,
        attr_name: str | None,
        command: str,
        msg: AstrBotMessage,
        meta: PlatformMetadata,
        session_id: str,
        kwarg_name: str | None = None,
    ) -> AstrMessageEvent | None:
        """尝试动态导入并创建平台特定事件，失败返回 None

        kwarg_name: 事件类构造函数的关键字参数名；None 时沿用 attr_name。
        平台实例属性名(attr_name)与事件参数名(kwarg_name)可能不同，
        如 qq_official：属性 self.client，但 QQOfficialMessageEvent(bot=...)。
        """
        try:
            import importlib
            platform = self._get_platform_instance(meta.id)
            logger.debug(f"_try_create_platform_event: 平台={platform_name}, platform_id={meta.id}")

            if not platform:
                logger.warning(f"无法获取 {platform_name} 平台实例")
                return None

            # 检查平台实例是否具有所需属性
            if attr_name and not hasattr(platform, attr_name):
                logger.warning(f"{platform_name} 平台实例缺少 '{attr_name}' 属性")
                return None

            module = importlib.import_module(module_path)
            event_cls = getattr(module, class_name)

            # 构建事件参数
            kwargs = {
                "message_str": command,
                "message_obj": msg,
                "platform_meta": meta,
                "session_id": session_id,
            }

            if attr_name == "adapter" or attr_name is None:
                # wechatpadpro 使用 adapter 参数
                kwargs["adapter"] = platform
            else:
                kwargs[kwarg_name or attr_name] = getattr(platform, attr_name)

            event = event_cls(**kwargs)

            # 设置平台实例引用
            if hasattr(event, 'platform_instance'):
                event.platform_instance = platform

            logger.debug(f"成功创建 {class_name}")
            return event

        except ImportError as e:
            logger.warning(f"导入 {platform_name} 事件类失败（可能框架版本不兼容）: {e}")
        except (AttributeError, TypeError) as e:
            logger.warning(f"创建 {platform_name} 事件实例失败: {e}")
        except Exception as e:
            logger.warning(f"创建 {platform_name} 事件时发生未知错误: {e}")

        return None

    def _create_webchat_event(
        self, command: str, msg: AstrBotMessage,
        meta: PlatformMetadata, session_id: str,
    ) -> AstrMessageEvent:
        """创建 WebChat 平台事件（无需平台实例）"""
        try:
            from astrbot.core.platform.sources.webchat.webchat_event import WebChatMessageEvent
            event = WebChatMessageEvent(
                message_str=command,
                message_obj=msg,
                platform_meta=meta,
                session_id=session_id,
            )
            logger.debug("成功创建 WebChatMessageEvent")
            return event
        except ImportError as e:
            logger.warning(f"导入 WebChatMessageEvent 失败: {e}")
        except (AttributeError, TypeError) as e:
            logger.warning(f"创建 WebChatMessageEvent 失败: {e}")

        return self._create_base_event(command, msg, meta, session_id)

    def _create_base_event(
        self, command: str, msg: AstrBotMessage,
        meta: PlatformMetadata, session_id: str,
    ) -> AstrMessageEvent:
        """创建基础事件对象（作为回退方案）"""
        event = AstrMessageEvent(
            message_str=command,
            message_obj=msg,
            platform_meta=meta,
            session_id=session_id,
        )

        # 设置必要属性
        event.unified_msg_origin = f"{meta.name}:{msg.type.value}:{session_id}"
        event.is_wake = True
        event.is_at_or_wake_command = True

        # 尝试设置平台实例引用
        try:
            platform_instance = self._get_platform_instance(meta.id)
            logger.debug(f"_create_base_event: 尝试获取平台实例，platform_id={meta.id}")
            if platform_instance:
                event.platform_instance = platform_instance
                logger.debug(f"为基础事件设置平台实例: {meta.id}")
            else:
                logger.warning(f"无法为基础事件获取平台实例: {meta.id}")
        except (AttributeError, TypeError) as e:
            logger.warning(f"设置基础事件平台实例失败: {e}")

        logger.debug("使用基础 AstrMessageEvent")
        return event