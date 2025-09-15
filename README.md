
<div align="center">

# astrbot_plugin_tts_llm

_✨ AstrBot LLM 回复语音合成插件 ✨_  

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-v3.4%2B-orange.svg)](https://github.com/AstrBotDevs/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-clown145-blue)](https://github.com/clown145)

</div>

## 📖 功能简介

本插件是为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 设计的一款高级语音合成工具。它能将 LLM 的文本回复无缝转换为带有多样情感的语音消息，赋予您的机器人更加生动和个性化的表达能力。

- **LLM回复自动语音化**：开启后，自动将LLM的文本回复翻译为日语并合成为语音。
- **动态情感管理**：通过指令自由注册、切换、查看不同的声音情感。
- **高度可配置**：支持自定义翻译API (OpenAI/Gemini)、TTS服务器地址和默认情感。
- **手动合成**：提供指令，可绕过 LLM 直接将指定文本合成为语音，方便测试。
- **故障转移**：支持配置多个TTS服务器，一个失效时自动尝试下一个。

---

## ⚠️ 重要前置：部署语音服务

本插件**自身不进行语音合成**，它依赖一个后端的 **Genie TTS 服务**。您必须先拥有一个可访问的该服务，插件才能正常工作。

> **Genie TTS** 是一个强大的语音合成项目，您需要将其部署为一个Web服务。
> - **官方仓库**: [https://github.com/High-Logic/Genie](https://github.com/High-Logic/Genie)

### 方案一：使用 Hugging Face 一键部署

算力免费而且无需本地机器配置，但是合成速度比较慢。

1.  **复制我的 Space**:
    -   服务仓库: [https://huggingface.co/spaces/clown145/genie-tts-t/tree/main](https://huggingface.co/spaces/clown145/genie-tts-t/tree/main)
    -   点击页面右上角的 **"Duplicate this Space"** 即可一键复制，拥有一个完全属于您自己的、免费的TTS服务。

2.  **使用自定义模型**:
    -   默认服务会从我的模型仓库 [clown145/my-genie-tts-models](https://huggingface.co/clown145/my-genie-tts-models/tree/main) 下载模型。该模型仓库已包含多个预置角色，例如： `kisaki` (月社妃), `hiy` (和泉妃爱), `may` (椎名真由理), `aoi` (葵) 等，您可以直接使用。
    -   若要使用您自己的模型，请将您训练和转换好的模型上传到您自己的 Hugging Face 模型仓库，然后在 Space 的 `app.py` 文件中修改 `REPO_ID` 和 `CHARACTERS` 字典。
    -   **【关键步骤】** 在您的模型仓库中，除了包含模型的 `models/` 文件夹外，**您必须创建一个名为 `reference_audio` 的文件夹**，并将所有用于注册情感的参考音频文件（如 `.wav`, `.ogg`）放入其中。
    -   **注意：** Genie 服务目前有加载3个模型的上限，请确保 `CHARACTERS` 字典中启用的角色不超过3个。

### 方案二：本地或 Windows 部署

- 如果您想在本地运行，请参照 Genie 官方仓库的文档进行部署。
- 作者还提供了 **Windows 一键整合包**，极大简化了部署流程，详情请访问其 GitHub。

**部署完成后，请记下您的服务 URL (例如 `https://your-name-your-space.hf.space`)，后续配置插件时需要用到。**

---

## 📦 插件安装

- **方式一 (推荐)**: 在 AstrBot 的插件市场搜索 `astrbot_plugin_tts_llm`，点击安装，等待完成即可。

- **方式二 (手动)**: 若安装失败，可尝试克隆源码。
  ```bash
  # 进入 AstrBot 插件目录
  cd /path/to/your/AstrBot/data/plugins

  # 克隆仓库
  git clone https://github.com/clown145/astrbot_plugin_tts_llm.git

  # 重启 AstrBot
  ```

---

## ⚙️ 插件配置

安装后，在 AstrBot 的 WebUI 找到本插件并进入配置页面。

| 配置项 | 说明 | 示例 |
| :--- | :--- | :--- |
| **TTS 服务器地址列表** | **【核心】** 填入您上一步部署好的 Genie TTS 服务 URL。可配置多个实现故障转移。 | `["https://your-name.hf.space"]` |
| **是否附带原文** | 发送语音时，是否同时发送 LLM 生成的原始文本。 | `true` / `false` |
| **默认角色名 / 情感名** | 自动语音模式下使用的默认声音。**必须是已通过指令注册过的。** | `kisaki` / `开心` |
| **翻译API配置** | 用于将中文LLM回复翻译成日语。支持 `openai` 和 `gemini` 格式。 |  |
| ├ `base_url` | API 的基础地址。 | `https://api.openai.com/v1` |
| ├ `api_key` | 您的 API 密钥。 | `sk-xxxxxx` |
| ├ `model` | 用于翻译的模型。 | `gpt-4o-mini` |

---

## ⌨️ 使用说明

### 命令表

#### 感情管理

| 命令格式 | 说明 |
| :--- | :--- |
| `/注册感情 <角色名> <情感名> <参考音频路径> <音频对应文本>` | 注册一个新的情感，用于语音合成。<br>**注意**：`<参考音频路径>` **必须**是您上传到模型仓库 `reference_audio/` 文件夹下的**相对路径**，例如：`reference_audio/Kisaki_happy.wav`。 |
| `/删除感情 <角色名> <情感名>` | 删除一个已注册的情感。 |
| `/查看感情` | 列出所有已注册的角色及其拥有的情感。 |

#### 核心功能

| 命令 (别名) | 说明 |
| :--- | :--- |
| `/tts-llm` (`/开启语音合成`) | 为**当前对话**开启“LLM回复自动转语音”功能。 |
| `/tts-q` (`/关闭语音合成`) | 为**当前对话**关闭“LLM回复自动转语音”功能。 |
| `/sw` (`/切换感情`) <角色名> <情感名> | 在自动模式下，为**当前对话**临时切换使用的情感。 |
| `/合成 <角色名> <情感名> <要合成的文本>` | 手动合成语音。若文本含空格，建议用英文双引号 `"` 括起来。 |

### 💡 典型使用流程

1.  **部署服务**：按照【重要前置】部分的说明，成功部署您的 Genie TTS 服务并获取 URL。确保已创建 `reference_audio` 文件夹并上传了参考音频。
2.  **配置插件**：在 AstrBot WebUI 中正确填写 **TTS 服务器地址列表** 和 **翻译API** 信息。
3.  **注册情感**：使用 `/注册感情` 指令添加至少一个您想用的情感。<br>
    `示例: /注册感情 开心 kisaki reference_audio/Kisaki_802.ogg "ほら、ホタルもとても喜んでいます。"`
4.  **设置默认值**：回到 WebUI 配置，将“默认角色名”和“默认情感名”设为您刚刚注册的，并保存。
5.  **开启模式**：在与机器人私聊或在群聊中，发送 `/tts-llm`。
6.  **开始对话**：现在，机器人的所有LLM回复都将变成语音！
7.  **关闭模式**：发送 `/tts-q` 即可恢复发送文本。

## 📝 开发说明
本插件的开发过程得到了 AI 的大量协助。如果代码或功能中存在任何不妥之处，敬请谅解并通过 Issue 提出，感谢您的支持！

## 🤝 致谢

- 本插件的语音合成功能由 [**Genie TTS**](https://github.com/High-Logic/Genie) 库提供核心支持，由衷感谢原作者的杰出工作。
