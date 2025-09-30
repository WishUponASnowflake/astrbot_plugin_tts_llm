import asyncio
import httpx
import json
import os
import re
import uuid
import wave
from typing import Dict, Optional, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
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
    "1.1.0",
    "https://github.com/clown145/astrbot_plugin_tts_llm",
)
class LlmTtsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.active_sessions: Set[str] = set()
        self.w_active_sessions: Set[str] = set()
        self.session_emotions: Dict[str, Dict[str, str]] = {}
        self.session_w_settings: Dict[str, Dict[str, str]] = {}

        plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_tts_llm")
        os.makedirs(plugin_data_dir, exist_ok=True)
        self.emotions_file_path = plugin_data_dir / "emotions.json"
        self.emotions_data = self._load_emotions_from_file()

        self.tts_server_index = 0
        self.http_client = httpx.AsyncClient(timeout=300.0)
        logger.info("LLM TTS 插件已加载。")

    def _load_emotions_from_file(self) -> Dict:
        """从JSON文件加载感情数据"""
        if not os.path.exists(self.emotions_file_path):
            with open(self.emotions_file_path, "w", encoding="utf-8") as f:
                json.dump({}, f)
            return {}
        try:
            with open(self.emotions_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(
                    f"成功从 {self.emotions_file_path} 加载 {sum(len(v) for v in data.values())} 个感情配置。"
                )
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"加载感情文件失败: {e}")
            return {}

    def _save_emotions_to_file(self):
        """将感情数据保存到JSON文件"""
        try:
            with open(self.emotions_file_path, "w", encoding="utf-8") as f:
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
        if ".." in ref_audio_path or os.path.isabs(ref_audio_path):
            yield event.plain_result("❌ 错误：参考音频路径无效。它必须是一个相对路径，且不能包含 '..'。" )
            return

        if character_name not in self.emotions_data:
            self.emotions_data[character_name] = {}

        self.emotions_data[character_name][emotion_name] = {
            "ref_audio_path": ref_audio_path,
            "ref_audio_text": ref_audio_text,
        }

        if self._save_emotions_to_file():
            yield event.plain_result(
                f"✅ 感情 '{emotion_name}' 已成功注册到角色 '{character_name}' 下。"
            )
        else:
            self.emotions_data = self._load_emotions_from_file()
            yield event.plain_result("❌ 保存感情时发生错误，注册失败。")

    @filter.command("删除感情")
    async def delete_emotion_command(
        self, event: AstrMessageEvent, character_name: str, emotion_name: str
    ):
        """删除一个已注册的感情"""
        if character_name not in self.emotions_data:
            yield event.plain_result(f"❌ 错误：未找到角色 '{character_name}'。")
            return

        if emotion_name not in self.emotions_data[character_name]:
            yield event.plain_result(
                f"❌ 错误：角色 '{character_name}' 下未找到名为 '{emotion_name}' 的感情。"
            )
            return

        del self.emotions_data[character_name][emotion_name]
        if not self.emotions_data[character_name]:
            del self.emotions_data[character_name]

        if self._save_emotions_to_file():
            yield event.plain_result(
                f"✅ 已成功删除角色 '{character_name}' 的感情 '{emotion_name}'。"
            )
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
    async def direct_tts_command(
        self,
        event: AstrMessageEvent,
        character_name: str,
        emotion_name: str,
        text_to_synthesize: str,
    ):
        """根据角色和感情名直接合成语音"""
        emotion_data = self.emotions_data.get(character_name, {}).get(emotion_name)
        if not emotion_data:
            yield event.plain_result(
                f"❌ 未找到角色 '{character_name}' 的感情 '{emotion_name}'。请先使用 /注册感情 指令添加。"
            )
            return

        yield event.plain_result("收到合成请求，正在处理...")
        audio_path = await self._direct_synthesize_speech(
            character_name=character_name,
            ref_audio_path=emotion_data["ref_audio_path"],
            ref_audio_text=emotion_data["ref_audio_text"],
            text=text_to_synthesize,
            session_id_for_log=event.unified_msg_origin,
        )

        if audio_path:
            yield event.chain_result([Comp.Record(file=audio_path)])
        else:
            yield event.plain_result("语音合成失败，请检查服务器状态或日志。")
        event.stop_event()

    @filter.command("tts-llm", alias={"开启语音合成"})
    async def start_tts(self, event: AstrMessageEvent):
        """为当前会话开启LLM回复语音合成(固定感情)"""
        session_id = event.unified_msg_origin
        self.active_sessions.add(session_id)
        self.w_active_sessions.discard(session_id)
        default_char = self.config.get("default_character")
        default_emotion = self.config.get("default_emotion_name")
        logger.info(f"会话 [{session_id}] 的 LLM TTS 功能已开启。")
        yield event.plain_result(
            f"▶️ 本对话的LLM语音合成已开启。\n将使用默认感情: {default_char} - {default_emotion}"
        )

    @filter.command("tts-q", alias={"关闭语音合成"})
    async def stop_tts(self, event: AstrMessageEvent):
        """为当前会话关闭所有LLM回复语音合成"""
        session_id = event.unified_msg_origin
        self.active_sessions.discard(session_id)
        self.w_active_sessions.discard(session_id)
        logger.info(f"会话 [{session_id}] 的所有 LLM TTS 功能已关闭。")
        yield event.plain_result("⏹️ 本对话的所有LLM语音合成功能已关闭。")

    @filter.command("tts-w", alias={"开启自动情感识别"})
    async def start_tts_w(self, event: AstrMessageEvent):
        """为当前会话开启LLM回复语音合成(自动情感)"""
        session_id = event.unified_msg_origin
        self.w_active_sessions.add(session_id)
        self.active_sessions.discard(session_id)
        default_char = self.config.get("default_character")
        logger.info(f"会话 [{session_id}] 的 LLM 自动情感识别 TTS 功能已开启。")
        yield event.plain_result(
            f"▶️ 本对话的自动情感识别语音合成已开启。\n将使用默认角色: {default_char}"
        )
    
    @filter.command("tts-w-q", alias={"关闭自动情感识别"})
    async def stop_tts_w(self, event: AstrMessageEvent):
        """为当前会话关闭LLM自动情感语音合成"""
        session_id = event.unified_msg_origin
        self.w_active_sessions.discard(session_id)
        logger.info(f"会话 [{session_id}] 的 LLM 自动情感识别 TTS 功能已关闭。")
        yield event.plain_result("⏹️ 本对话的自动情感识别语音合成已关闭。")

    @filter.command("sw", alias={"切换感情"})
    async def switch_emotion(
        self, event: AstrMessageEvent, character_name: str, emotion_name: str
    ):
        """为当前会话切换(固定感情模式)下使用的感情"""
        if self.emotions_data.get(character_name, {}).get(emotion_name):
            self.session_emotions[event.unified_msg_origin] = {
                "character": character_name,
                "emotion": emotion_name,
            }
            logger.info(
                f"会话 [{event.unified_msg_origin}] 切换感情至: {character_name} - {emotion_name}"
            )
            yield event.plain_result(
                f"本会话感情已切换为: {character_name} - {emotion_name}"
            )
        else:
            yield event.plain_result(
                f"❌ 未找到角色 '{character_name}' 的感情 '{emotion_name}'。"
            )

    @filter.command("sw-w", alias={"切换w角色"})
    async def switch_w_character(self, event: AstrMessageEvent, character_name: str):
        """为当前会话切换(自动情感模式)下使用的角色"""
        if character_name in self.emotions_data:
            self.session_w_settings[event.unified_msg_origin] = {
                "character": character_name
            }
            logger.info(
                f"会话 [{event.unified_msg_origin}] 切换自动情感识别角色至: {character_name}"
            )
            yield event.plain_result(
                f"本会话自动情感识别角色已切换为: {character_name}"
            )
        else:
            yield event.plain_result(f"❌ 未找到角色 '{character_name}'。")

    @filter.on_llm_response()
    async def intercept_llm_response_for_tts(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """在LLM请求完成后，捕获其文本结果并进行语音合成"""
        session_id = event.unified_msg_origin
        audio_path: Optional[str] = None
        original_text = resp.completion_text.strip()
        if not original_text:
            return

        if session_id in self.w_active_sessions:
            logger.info(f"[{session_id}] 捕获到LLM文本，准备进行自动情感语音合成: {original_text}")
            session_setting = self.session_w_settings.get(session_id)
            char_name = session_setting["character"] if session_setting else self.config.get("default_character")

            if not char_name:
                resp.result_chain.chain.append(Comp.Plain("\n(语音合成失败: 未配置角色)"))
                return

            character_emotions = self.emotions_data.get(char_name, {})
            if not character_emotions:
                resp.result_chain.chain.append(Comp.Plain(f"\n(语音合成失败: 角色'{char_name}'无可用感情)"))
                return
            
            api_config = self.config.get("translation_api", {})
            w_prompt_template = api_config.get("w_mode_prompt")
            
            if not w_prompt_template:
                logger.error("自动情感识别模式的提示词模板(w_mode_prompt)未在配置中找到或为空！")
                resp.result_chain.chain.append(Comp.Plain("\n(语音合成失败: 缺少提示词配置)"))
                return

            emotion_list_str = ", ".join(character_emotions.keys())
            augmented_prompt = w_prompt_template.format(emotion_list=emotion_list_str, text=original_text)
            
            japanese_text_with_emotion = await self._translate_to_japanese(augmented_prompt)
            if not japanese_text_with_emotion:
                resp.result_chain.chain.append(Comp.Plain("\n(翻译或情感识别失败)"))
                return

            logger.info(f"[{session_id}] 翻译及情感识别结果: {japanese_text_with_emotion}")
            match = re.search(r'(.*)\[(.+?)\]\s*$', japanese_text_with_emotion.strip(), re.DOTALL)
            
            if not match:
                resp.result_chain.chain.append(Comp.Plain("\n(语音合成失败: 无法解析情感)"))
                return

            japanese_text, emotion_name = match.group(1).strip(), match.group(2).strip()
            emotion_data = character_emotions.get(emotion_name)

            if not emotion_data:
                resp.result_chain.chain.append(Comp.Plain(f"\n(语音合成失败: 情感'{emotion_name}'无效或不存在于'{char_name}'下)"))
                return

            logger.info(f"[{session_id}] 识别到情感 '{emotion_name}'，使用该情感合成语音。")
            audio_path = await self._direct_synthesize_speech(
                character_name=char_name,
                ref_audio_path=emotion_data["ref_audio_path"],
                ref_audio_text=emotion_data["ref_audio_text"],
                text=japanese_text,
                session_id_for_log=session_id,
            )

        elif session_id in self.active_sessions:
            logger.info(f"[{session_id}] 捕获到LLM文本，准备语音合成: {original_text}")
            japanese_text = await self._translate_to_japanese(original_text)
            if not japanese_text:
                resp.result_chain.chain.append(Comp.Plain("\n(翻译失败)"))
                return

            logger.info(f"[{session_id}] 翻译结果: {japanese_text}")
            audio_path = await self._synthesize_speech_from_context(japanese_text, session_id)
        
        else:
            return

        if audio_path:
            logger.info(f"[{session_id}] 语音合成成功: {audio_path}")
            resp.result_chain.chain = [Comp.Record(file=audio_path)]
            if self.config.get("send_text_with_audio", False):
                resp.result_chain.chain.append(Comp.Plain(f"{original_text}"))
        else:
            logger.error(f"[{session_id}] 语音合成失败")
            resp.result_chain.chain.append(Comp.Plain("\n(语音合成失败)"))

    async def _translate_to_japanese(self, text: str) -> Optional[str]:
        api_config = self.config.get("translation_api", {})
        base_url, api_key, prompt, model, api_format = (
            api_config.get("base_url"),
            api_config.get("api_key"),
            api_config.get("prompt"),
            api_config.get("model", "gpt-3.5-turbo"),
            api_config.get("api_format", "openai"),
        )
        if not all([base_url, api_key]):
            logger.error("翻译API配置不完整 (base_url, api_key)。")
            return None
            
        system_prompt = prompt if prompt else "You are a translation assistant."
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        endpoint_url = base_url
        if api_format == "openai":
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
            }
            endpoint_url = f"{base_url.strip('/')}/chat/completions"
        elif api_format == "gemini":
            payload = {
                "contents": [{"parts": [{"text": text}]}],
                "systemInstruction": {"parts": [{"text": system_prompt}]}
            }
            endpoint_url = f"{base_url.strip('/')}/v1beta/models/{model}:generateContent?key={api_key}"
            headers.pop("Authorization", None)
        else:
            logger.error(f"不支持的API格式: {api_format}")
            return None
        try:
            response = await self.http_client.post(
                endpoint_url, headers=headers, json=payload
            )
            response.raise_for_status()
            data = response.json()
            if api_format == "openai":
                return data["choices"][0]["message"]["content"]
            elif api_format == "gemini":
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.error(f"翻译请求失败: {e}\n响应: {getattr(response, 'text', 'N/A')}")
            return None

    async def _synthesize_speech_from_context(
        self, text: str, session_id: str
    ) -> Optional[str]:
        session_setting = self.session_emotions.get(session_id)
        if session_setting:
            char_name, emotion_name = (
                session_setting["character"],
                session_setting["emotion"],
            )
        else:
            char_name, emotion_name = (
                self.config.get("default_character"),
                self.config.get("default_emotion_name"),
            )
        if not char_name or not emotion_name:
            logger.error(f"[{session_id}] 未配置默认角色或感情。")
            return None
        emotion_data = self.emotions_data.get(char_name, {}).get(emotion_name)
        if not emotion_data:
            logger.error(f"[{session_id}] 找不到感情配置: {char_name} - {emotion_name}")
            return None
        return await self._direct_synthesize_speech(
            character_name=char_name,
            ref_audio_path=emotion_data["ref_audio_path"],
            ref_audio_text=emotion_data["ref_audio_text"],
            text=text,
            session_id_for_log=session_id,
        )

    async def _attempt_synthesis_on_server(
        self,
        server_url: str,
        character_name: str,
        ref_audio_path: str,
        ref_audio_text: str,
        text: str,
        session_id_for_log: str,
    ) -> Optional[str]:
        """使用单个指定的TTS服务器尝试合成语音。"""
        logger.info(f"[{session_id_for_log}] 尝试TTS服务器: {server_url}")
        try:
            ref_payload = {
                "character_name": character_name,
                "audio_path": ref_audio_path,
                "audio_text": ref_audio_text,
            }
            response = await self.http_client.post(
                f"{server_url}/set_reference_audio", json=ref_payload, timeout=60
            )
            response.raise_for_status()
            tts_payload = {
                "character_name": character_name,
                "text": text,
                "split_sentence": True,
            }
            async with self.http_client.stream(
                "POST", f"{server_url}/tts", json=tts_payload, timeout=300
            ) as response_tts:
                response_tts.raise_for_status()
                output_dir = os.path.join("data", "temp_audio")
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, f"{uuid.uuid4()}.wav")
                with wave.open(output_path, "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(BYTES_PER_SAMPLE)
                    wf.setframerate(SAMPLE_RATE)
                    async for chunk in response_tts.aiter_bytes():
                        wf.writeframes(chunk)
                return output_path
        except Exception as e:
            logger.warning(
                f"[{session_id_for_log}] TTS服务器 {server_url} 交互失败: {e}"
            )
            return None

    async def _direct_synthesize_speech(
        self,
        character_name: str,
        ref_audio_path: str,
        ref_audio_text: str,
        text: str,
        session_id_for_log: str,
    ) -> Optional[str]:
        servers = self.config.get("tts_servers", [])
        if not servers:
            logger.error(f"[{session_id_for_log}] 未配置TTS服务器。")
            return None

        start_index = self.tts_server_index
        for i in range(len(servers)):
            current_index = (start_index + i) % len(servers)
            server_url = servers[current_index].strip("/")
            
            if i == 0:
                self.tts_server_index = (start_index + 1) % len(servers)

            audio_path = await self._attempt_synthesis_on_server(
                server_url=server_url,
                character_name=character_name,
                ref_audio_path=ref_audio_path,
                ref_audio_text=ref_audio_text,
                text=text,
                session_id_for_log=session_id_for_log,
            )
            if audio_path:
                return audio_path

        logger.error(f"[{session_id_for_log}] 尝试所有TTS服务器后合成失败。")
        return None

    async def terminate(self):
        """插件卸载/停用时关闭http客户端"""
        await self.http_client.aclose()
        logger.info("LLM TTS 插件已卸载，HTTP客户端已关闭。")
