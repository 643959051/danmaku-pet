"""开播电脑客户端包：接收云端指令、本地 TTS/动作、回执。"""

from danmaku_pet.client.executor import LocalExecutor
from danmaku_pet.client.dispatcher import CommandDispatcher
from danmaku_pet.client.connection import ClientConnection

__all__ = ["ClientConnection", "CommandDispatcher", "LocalExecutor"]
