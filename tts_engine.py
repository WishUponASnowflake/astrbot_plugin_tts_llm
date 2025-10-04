import asyncio
import os
import re
import uuid
import wave
from typing import Optional, List, Dict

import httpx
from astrbot.api import logger, AstrBotConfig

# --- 音频参数 (必须与Genie TTS服务输出匹配) ---
BYTES_PER_SAMPLE = 2
CHANNELS = 1
SAMPLE_RATE = 32000


class TTSEngine:
    """处理所有与TTS合成相关的核心逻辑，包括文本分块、并发合成和音频合并"""

    def __init__(self, config: AstrBotConfig, http_client: httpx.AsyncClient):
        self.config = config
        self.http_client = http_client
        self.tts_server_index = 0

    def _split_text_into_chunks(self, text: str, sentences_per_chunk: int) -> list[str]:
        """根据标点将文本切分为句子，再按指定数量合并成块"""
        if sentences_per_chunk <= 0:
            return [text]
            
        regex_pattern = self.config.get("sentence_split_regex", r'([。、，！？,.!?])')
        
        sentences = re.split(regex_pattern, text)
        if not sentences:
            return []

        full_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i]
            delimiter = sentences[i+1] if i+1 < len(sentences) else ""
            if sentence:
                full_sentences.append(sentence + delimiter)
        if len(sentences) % 2 == 1 and sentences[-1]:
            full_sentences.append(sentences[-1])

        chunks = []
        for i in range(0, len(full_sentences), sentences_per_chunk):
            chunk = "".join(full_sentences[i:i + sentences_per_chunk])
            chunks.append(chunk)
            
        logger.info(f"文本已切分为 {len(chunks)} 个块。")
        return chunks

    async def _merge_wav_files(self, input_paths: list[str]) -> Optional[str]:
        """以无损的方式将多个WAV文件按顺序合并为一个，并清理分块文件。"""
        if not input_paths:
            return None
        
        output_dir = os.path.join("data", "temp_audio")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{uuid.uuid4()}_merged.wav")

        try:
            with wave.open(input_paths[0], 'rb') as wf_in:
                params = wf_in.getparams()

            with wave.open(output_path, 'wb') as wf_out:
                wf_out.setparams(params)
                for file_path in input_paths:
                    with wave.open(file_path, 'rb') as wf_in:
                        wf_out.writeframes(wf_in.readframes(wf_in.getnframes()))
            
            logger.info(f"成功将 {len(input_paths)} 个音频文件合并到: {output_path}")
            
            for file_path in input_paths:
                try:
                    os.remove(file_path)
                except OSError as e:
                    logger.warning(f"删除临时文件 {file_path} 失败: {e}")
                    
            return output_path
        except Exception as e:
            logger.error(f"合并WAV文件时出错: {e}")
            return None

    async def _attempt_synthesis_on_server(
        self, server_url: str, character_name: str, ref_audio_path: str,
        ref_audio_text: str, text: str, session_id_for_log: str,
    ) -> Optional[str]:
        """使用单个指定的TTS服务器尝试合成语音，并返回保存好的文件路径。"""
        logger.info(f"[{session_id_for_log}] 尝试TTS服务器: {server_url}")
        try:
            ref_payload = {
                "character_name": character_name, "audio_path": ref_audio_path, "audio_text": ref_audio_text,
            }
            response = await self.http_client.post(f"{server_url}/set_reference_audio", json=ref_payload, timeout=60)
            response.raise_for_status()

            tts_payload = {"character_name": character_name, "text": text, "split_sentence": True}
            async with self.http_client.stream("POST", f"{server_url}/tts", json=tts_payload, timeout=300) as response_tts:
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
            logger.warning(f"[{session_id_for_log}] TTS服务器 {server_url} 交互失败: {e}")
            return None
        
    async def _synthesis_worker(
        self, worker_id: int, task_queue: asyncio.Queue, results_list: list,
        character_name: str, ref_audio_path: str, ref_audio_text: str, session_id_for_log: str,
    ):
        """单个TTS服务器的工作进程，从队列中获取任务并处理"""
        servers = self.config.get("tts_servers", [])
        num_servers = len(servers)
        
        while not task_queue.empty():
            try:
                task_index, chunk_text = await task_queue.get()
            except asyncio.CancelledError:
                break
            
            start_server_idx = worker_id % num_servers
            audio_path = None
            for i in range(num_servers):
                server_idx = (start_server_idx + i) % num_servers
                server_url = servers[server_idx].strip("/")
                log_id = f"{session_id_for_log}-chunk-{task_index+1}"
                
                audio_path = await self._attempt_synthesis_on_server(
                    server_url, character_name, ref_audio_path, ref_audio_text, chunk_text, log_id
                )
                if audio_path:
                    logger.info(f"[Worker-{worker_id}] 成功合成块 {task_index+1} 于服务器 {server_url}")
                    results_list[task_index] = audio_path
                    break
            
            if not audio_path:
                logger.error(f"[Worker-{worker_id}] 块 {task_index+1} 尝试所有服务器后仍然失败。")
                results_list[task_index] = None

            task_queue.task_done()

    async def synthesize(
        self, character_name: str, ref_audio_path: str, ref_audio_text: str, text: str, session_id_for_log: str,
    ) -> Optional[str]:
        """执行语音合成的核心入口点，支持并发处理"""
        servers = self.config.get("tts_servers", [])
        if not servers:
            logger.error(f"[{session_id_for_log}] 未配置TTS服务器。")
            return None

        if self.config.get("enable_sentence_splitting", False):
            sentences_per_chunk = self.config.get("sentences_per_chunk", 2)
            text_chunks = self._split_text_into_chunks(text, sentences_per_chunk)
            
            if len(text_chunks) > 1:
                task_queue = asyncio.Queue()
                for i, chunk in enumerate(text_chunks):
                    task_queue.put_nowait((i, chunk))

                results_list = [None] * len(text_chunks)
                workers = [
                    asyncio.create_task(
                        self._synthesis_worker(
                            worker_id=i, task_queue=task_queue, results_list=results_list,
                            character_name=character_name, ref_audio_path=ref_audio_path,
                            ref_audio_text=ref_audio_text, session_id_for_log=session_id_for_log,
                        )
                    ) for i in range(len(servers))
                ]

                logger.info(f"[{session_id_for_log}] 创建了 {len(workers)} 个worker来处理 {len(text_chunks)} 个语音块...")
                await task_queue.join()
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

                successful_paths = [path for path in results_list if path]
                if not successful_paths:
                    logger.error(f"[{session_id_for_log}] 所有语音块都合成失败。")
                    return None
                
                return successful_paths[0] if len(successful_paths) == 1 else await self._merge_wav_files(successful_paths)

        # 如果不切分，则使用轮询逻辑
        logger.info(f"[{session_id_for_log}] 使用单块模式进行合成。")
        start_index = self.tts_server_index
        for i in range(len(servers)):
            current_index = (start_index + i) % len(servers)
            server_url = servers[current_index].strip("/")
            
            if i == 0:
                self.tts_server_index = (start_index + 1) % len(servers)

            audio_path = await self._attempt_synthesis_on_server(
                server_url=server_url, character_name=character_name,
                ref_audio_path=ref_audio_path, ref_audio_text=ref_audio_text,
                text=text, session_id_for_log=session_id_for_log,
            )
            if audio_path:
                return audio_path

        logger.error(f"[{session_id_for_log}] 尝试所有TTS服务器后合成失败。")
        return None