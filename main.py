import asyncio
import httpx
import json
import os
import uuid
import wave
from typing import Dict, Optional, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.api.provider import LLMResponse

# --- 音频参数 (必须与Genie TTS服务输出匹配) ---
BYTES_PER_SAMPLE = 2
CHANNELS = 1
SAMPLE_RATE = 32000

@register(
    "astrbot_plugin_tts_llm",
    "clown145",
    "一个通过LLM、翻译和TTS实现语音合成的插件",
    "1.0.0", 
    "https://github.com/clown145/astrbot_plugin_tts_llm"
)
class LlmTtsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.active_sessions: Set[str] = set()
        self.session_emotions: Dict[str, Dict[str, str]] = {}
        
        # 感情数据相关
        plugin_data_dir = os.path.join("data", "plugin_data","astrbot_plugin_tts_llm")
        os.makedirs(plugin_data_dir, exist_ok=True)
        self.emotions_file_path = os.path.join(plugin_data_dir, "emotions.json")
        self.emotions_data = self._load_emotions_from_file()

        self.tts_server_index = 0
        self.http_client = httpx.AsyncClient(timeout=300.0)
        logger.info("LLM TTS 插件已加载。")

    def _load_emotions_from_file(self) -> Dict:
        """从JSON文件加载感情数据"""
        if not os.path.exists(self.emotions_file_path):
            with open(self.emotions_file_path, 'w', encoding='utf-8') as f:
                json.dump({}, f)
            return {}
        try:
            with open(self.emotions_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"成功从 {self.emotions_file_path} 加载 {sum(len(v) for v in data.values())} 个感情配置。")
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载感情文件失败: {e}")
            return {}

    def _save_emotions_to_file(self):
        """将感情数据保存到JSON文件"""
        try:
            with open(self.emotions_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.emotions_data, f, ensure_ascii=False, indent=4)
            return True
        except IOError as e:
            logger.error(f"保存感情文件失败: {e}")
            return False

    @filter.command("注册感情")
    async def register_emotion_command(
        self,
        event: AstrMessageEvent,
        character_name: str,
        emotion_name: str,
        ref_audio_path: str,
        ref_audio_text: str,
    ):
        """注册一个新的感情并保存到文件"""
        if character_name not in self.emotions_data:
            self.emotions_data[character_name] = {}
        
        self.emotions_data[character_name][emotion_name] = {
            "ref_audio_path": ref_audio_path,
            "ref_audio_text": ref_audio_text,
        }
        
        if self._save_emotions_to_file():
            yield event.plain_result(f"✅ 感情 '{emotion_name}' 已成功注册到角色 '{character_name}' 下。")
        else:
            self.emotions_data = self._load_emotions_from_file()
            yield event.plain_result("❌ 保存感情时发生错误，注册失败。")

    @filter.command("删除感情")
    async def delete_emotion_command(self, event: AstrMessageEvent, character_name: str, emotion_name: str):
        """删除一个已注册的感情"""
        if character_name not in self.emotions_data:
            yield event.plain_result(f"❌ 错误：未找到角色 '{character_name}'。")
            return
        
        if emotion_name not in self.emotions_data[character_name]:
            yield event.plain_result(f"❌ 错误：角色 '{character_name}' 下未找到名为 '{emotion_name}' 的感情。")
            return
            
        # 执行删除
        del self.emotions_data[character_name][emotion_name]
        
        # 如果角色下已无任何感情，则删除该角色键
        if not self.emotions_data[character_name]:
            del self.emotions_data[character_name]
            
        if self._save_emotions_to_file():
            yield event.plain_result(f"✅ 已成功删除角色 '{character_name}' 的感情 '{emotion_name}'。")
        else:
            self.emotions_data = self._load_emotions_from_file()
            yield event.plain_result("❌ 保存文件时发生错误，删除失败。")

    @filter.command("查看感情")
    async def view_emotions_command(self, event: AstrMessageEvent):
        """查看所有已注册的感情"""
        if not self.emotions_data:
            yield event.plain_result("当前未注册任何感情。")
            return
            
        formatted_lines = ["所有已注册的感情列表："]
        for character, emotions in self.emotions_data.items():
            formatted_lines.append(f"\n角色: {character}")
            if emotions:
                for emotion_name in emotions.keys():
                    formatted_lines.append(f"  - {emotion_name}")
            else:
                 formatted_lines.append("  (暂无感情)")
                 
        final_message = "\n".join(formatted_lines)
        yield event.plain_result(final_message)

    @filter.command("合成")
    async def direct_tts_command(self, event: AstrMessageEvent, character_name: str, emotion_name: str, text_to_synthesize: str):
        """根据角色和感情名直接合成语音"""
        emotion_data = self.emotions_data.get(character_name, {}).get(emotion_name)
        if not emotion_data:
            yield event.plain_result(f"❌ 未找到角色 '{character_name}' 的感情 '{emotion_name}'。请先使用 /注册感情 指令添加。")
            return

        yield event.plain_result("收到合成请求，正在处理...")
        audio_path = await self._direct_synthesize_speech(
            character_name=character_name, ref_audio_path=emotion_data["ref_audio_path"],
            ref_audio_text=emotion_data["ref_audio_text"], text=text_to_synthesize,
            session_id_for_log=event.unified_msg_origin,
        )

        if audio_path:
            yield event.chain_result([Comp.Record(file=audio_path)])
        else:
            yield event.plain_result("语音合成失败，请检查服务器状态或日志。")
        event.stop_event()

    @filter.command("tts-llm", alias={'开启语音合成'})
    async def start_tts(self, event: AstrMessageEvent):
        """为当前会话开启LLM回复语音合成"""
        session_id = event.unified_msg_origin
        self.active_sessions.add(session_id)
        default_char = self.config.get("default_character")
        default_emotion = self.config.get("default_emotion_name")
        logger.info(f"会话 [{session_id}] 的 LLM TTS 功能已开启。")
        yield event.plain_result(f"▶️ 本对话的LLM语音合成已开启。\n将使用默认感情: {default_char} - {default_emotion}")

    @filter.command("tts-q", alias={'关闭语音合成'})
    async def stop_tts(self, event: AstrMessageEvent):
        """为当前会话关闭LLM回复语音合成"""
        session_id = event.unified_msg_origin
        self.active_sessions.discard(session_id)
        logger.info(f"会话 [{session_id}] 的 LLM TTS 功能已关闭。")
        yield event.plain_result("⏹️ 本对话的LLM语音合成已关闭。")

    @filter.command("sw", alias={'切换感情'})
    async def switch_emotion(self, event: AstrMessageEvent, character_name: str, emotion_name: str):
        """为当前会话切换自动合成时使用的感情"""
        if self.emotions_data.get(character_name, {}).get(emotion_name):
            self.session_emotions[event.unified_msg_origin] = {"character": character_name, "emotion": emotion_name}
            logger.info(f"会话 [{event.unified_msg_origin}] 切换感情至: {character_name} - {emotion_name}")
            yield event.plain_result(f"本会话感情已切换为: {character_name} - {emotion_name}")
        else:
            yield event.plain_result(f"❌ 未找到角色 '{character_name}' 的感情 '{emotion_name}'。")

    @filter.on_llm_response()
    async def intercept_llm_response_for_tts(self, event: AstrMessageEvent, resp: LLMResponse):
        """在LLM请求完成后，捕获其文本结果并进行语音合成"""
        if event.unified_msg_origin not in self.active_sessions: return
        original_text = resp.completion_text.strip()
        if not original_text: return

        logger.info(f"[{event.unified_msg_origin}] 捕获到LLM文本，准备语音合成: {original_text}")
        japanese_text = await self._translate_to_japanese(original_text)
        if not japanese_text:
            logger.error(f"[{event.unified_msg_origin}] 翻译失败")
            resp.result_chain.chain.append(Comp.Plain("\n(翻译失败)"))
            return
        
        logger.info(f"[{event.unified_msg_origin}] 翻译结果: {japanese_text}")
        audio_path = await self._synthesize_speech_from_context(japanese_text, event.unified_msg_origin)
        if not audio_path:
            logger.error(f"[{event.unified_msg_origin}] 语音合成失败")
            resp.result_chain.chain.append(Comp.Plain("\n(语音合成失败)"))
            return

        logger.info(f"[{event.unified_msg_origin}] 语音合成成功: {audio_path}")
        resp.result_chain.chain = [Comp.Record(file=audio_path)]
        if self.config.get("send_text_with_audio", False):
            resp.result_chain.chain.append(Comp.Plain(f"{original_text}"))

    async def _translate_to_japanese(self, text: str) -> Optional[str]:
        api_config = self.config.get("translation_api", {})
        base_url, api_key, prompt, model, api_format = (
            api_config.get("base_url"), api_config.get("api_key"), api_config.get("prompt"),
            api_config.get("model", "gpt-3.5-turbo"), api_config.get("api_format", "openai")
        )
        if not all([base_url, api_key, prompt]):
            logger.error("翻译API配置不完整。"); return None
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        endpoint_url = base_url
        if api_format == "openai":
            payload = { "model": model, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}] }
            endpoint_url = f"{base_url.strip('/')}/chat/completions"
        elif api_format == "gemini":
            payload = { "contents": [{"parts": [{"text": text}]}], "systemInstruction": {"parts": [{"text": prompt}]} }
            endpoint_url = f"{base_url.strip('/')}/v1beta/models/{model}:generateContent?key={api_key}"
            headers.pop("Authorization", None)
        else:
            logger.error(f"不支持的API格式: {api_format}"); return None
        try:
            response = await self.http_client.post(endpoint_url, headers=headers, json=payload)
            response.raise_for_status(); data = response.json()
            if api_format == "openai": return data["choices"][0]["message"]["content"]
            elif api_format == "gemini": return data['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            logger.error(f"翻译请求失败: {e}\n响应: {getattr(response, 'text', 'N/A')}"); return None
            
    async def _synthesize_speech_from_context(self, text: str, session_id: str) -> Optional[str]:
        session_setting = self.session_emotions.get(session_id)
        if session_setting:
            char_name, emotion_name = session_setting["character"], session_setting["emotion"]
        else:
            char_name, emotion_name = self.config.get("default_character"), self.config.get("default_emotion_name")
        if not char_name or not emotion_name:
            logger.error(f"[{session_id}] 未配置默认角色或感情。"); return None
        emotion_data = self.emotions_data.get(char_name, {}).get(emotion_name)
        if not emotion_data:
            logger.error(f"[{session_id}] 找不到感情配置: {char_name} - {emotion_name}"); return None
        return await self._direct_synthesize_speech(
            character_name=char_name, ref_audio_path=emotion_data["ref_audio_path"],
            ref_audio_text=emotion_data["ref_audio_text"], text=text, session_id_for_log=session_id
        )

    async def _direct_synthesize_speech(self, character_name: str, ref_audio_path: str, ref_audio_text: str, text: str, session_id_for_log: str) -> Optional[str]:
        try:
            servers = json.loads(self.config.get("tts_servers", "[]"))
        except json.JSONDecodeError:
            logger.error(f"[{session_id_for_log}] TTS服务器配置错误。")
            return None
        if not servers:
            logger.error(f"[{session_id_for_log}] 未配置TTS服务器。")
            return None

        start_index = self.tts_server_index

        self.tts_server_index = (self.tts_server_index + 1) % len(servers)

        for i in range(len(servers)):
            current_index = (start_index + i) % len(servers)
            base_url = servers[current_index].strip('/')
            logger.info(f"[{session_id_for_log}] 尝试TTS服务器: {base_url}")
            try:
                ref_payload = {"character_name": character_name, "audio_path": ref_audio_path, "audio_text": ref_audio_text}
                response = await self.http_client.post(f"{base_url}/set_reference_audio", json=ref_payload, timeout=60)
                response.raise_for_status()
                tts_payload = {"character_name": character_name, "text": text, "split_sentence": True}
                async with self.http_client.stream("POST", f"{base_url}/tts", json=tts_payload, timeout=300) as response_tts:
                    response_tts.raise_for_status()
                    output_dir = os.path.join("data", "temp_audio")
                    os.makedirs(output_dir, exist_ok=True)
                    output_path = os.path.join(output_dir, f"{uuid.uuid4()}.wav")
                    with wave.open(output_path, 'wb') as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(BYTES_PER_SAMPLE)
                        wf.setframerate(SAMPLE_RATE)
                        async for chunk in response_tts.aiter_bytes():
                            wf.writeframes(chunk)

                    return output_path
            except Exception as e:
                logger.warning(f"[{session_id_for_log}] TTS服务器 {base_url} 交互失败: {e}")

        logger.error(f"[{session_id_for_log}] 尝试所有TTS服务器后合成失败。")
        return None

    async def terminate(self):
        """插件卸载/停用时关闭http客户端"""
        await self.http_client.aclose()
        logger.info("LLM TTS 插件已卸载，HTTP客户端已关闭。")