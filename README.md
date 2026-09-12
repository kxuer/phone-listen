# Phone Listen — 手机语音输入到电脑

手机当作麦克风，按住说话、松开即输入。语音通过 Wi-Fi 实时传到电脑，由本地语音识别引擎转写成文字，自动粘贴到当前光标所在的输入框。

## 工作流程

```
手机 App（按住说话）  ── PCM 音频流（16kHz/mono/16bit）──▶  电脑 listener.py（TCP:8989）
                                                                │
                                                                ├─ faster-whisper / Vosk 识别
                                                                │
                                                                └─ 剪贴板 + Ctrl+V → 光标处输入文字
```

## 项目结构

```
phone-listen/
├── android-app/          # Android 手机端
│   ├── app/              # Kotlin 源码 + 布局 + 图标
│   ├── build_apk.bat     # Docker 一键编译 APK
│   └── Dockerfile        # 编译环境
└── windows-listener/     # Windows 电脑端
    ├── listener.py       # 主程序：接收音频 → 识别 → 粘贴
    ├── start_listener.bat        # 启动脚本（默认 faster-whisper）
    ├── start_listener_large.bat  # 启动脚本（Vosk 大模型）
    ├── tip.md            # 语音转写提示词模板
    └── model/            # 模型目录（自动下载，无需手动管理）
```

## 快速开始

### 1. 电脑端

```bash
# 进入 windows-listener 目录
cd windows-listener

# 双击或运行启动脚本（自动安装依赖 + 下载模型）
start_listener.bat
```

首次运行会自动安装 `faster-whisper`、`vosk`、`pyautogui` 等依赖，并下载默认的 faster-whisper small 模型（约 484MB，仅一次）。

### 2. 手机端

1. 用 `build_apk.bat` 编译 APK（需要 Docker），或直接安装已编译的 `app-debug.apk`
2. 打开 App，填写电脑的局域网 IP
3. 按住按钮说话，松开即可将文字输入到电脑光标处

### 3. 电脑端防火墙

确保 Windows 防火墙允许 `8989` 端口入站（首次运行 Windows 会弹出授权提示）。

## 语音识别引擎

默认使用 **faster-whisper**（精度高、带标点），可选 **Vosk**（速度极快）。通过 `--model` 参数切换：

| 命令 | 引擎 | 模型体积 | 特点 |
|------|------|----------|------|
| `start_listener.bat` | faster-whisper small | ~484MB | 默认，精度高，自动加标点 |
| `python listener.py --model whisper-medium` | faster-whisper medium | ~1.5GB | 更高精度 |
| `python listener.py --model whisper-large-v3` | faster-whisper large-v3 | ~3GB | 最高精度 |
| `python listener.py --model whisper-tiny` | faster-whisper tiny | ~75MB | 最快，适合低配 GPU |
| `start_listener_large.bat` | Vosk large | 1.3GB | Vosk 中精度最高 |
| `python listener.py --model small` | Vosk small | 42MB | 最轻量，纯 CPU |
| `python listener.py --model /path/to/vosk-model` | Vosk 自定义 | — | 本地模型目录 |

所有模型首次使用时自动下载。Vosk 模型放在 `model/` 目录，Whisper 模型放在 `model/` 目录（本地缓存模式）或 HuggingFace 缓存目录。国内网络环境下 Whisper 模型会自动切换到 hf-mirror.com 镜像。

## 常用选项

```bash
python listener.py --play              # 识别的同时播放声音（听到自己说话）
python listener.py --save              # 保存每次录音为 wav 到 recordings/
python listener.py --no-type           # 只识别打印，不粘贴（测试用）
python listener.py --compute-type float32  # whisper 推理精度（默认 float16，GPU 更快）
python listener.py --port 9000         # 自定义端口（默认 8989）
```

## 技术细节

- **音频格式**：16kHz / 单声道 / 16-bit PCM，与手机端一致
- **传输方式**：TCP 原始 PCM 流，按住即传、松开断连
- **识别策略**：
  - Vosk：流式识别，实时喂入 PCM，松开后取最终结果
  - Whisper：攒完整段音频，松开后一次性转写（带 VAD 过滤静音段）
- **文字输入**：通过剪贴板 + Ctrl+V 粘贴到当前光标位置，粘贴后 1 秒恢复原剪贴板内容
- **短语音过滤**：Whisper 引擎会丢弃短于 0.25 秒的音频

## 依赖

### 电脑端（Python）

- Python 3.10+
- `faster-whisper` — Whisper 识别引擎
- `vosk` — Vosk 识别引擎
- `pyautogui` — 模拟键盘输入
- `pyperclip` — 剪贴板操作
- `sounddevice` — 音频播放（`--play` 时需要）
- `numpy` — Whisper 音频处理

### 手机端

- Android 8.0+（API 26）
- 麦克风权限、网络权限

## 编译 APK

```bash
cd android-app
build_apk.bat
```

使用 Docker 编译，无需本地安装 Android SDK。产物在 `app/build/outputs/apk/debug/app-debug.apk`。

## 已知限制

- 系统自带的某些 TTS 语音（如 Windows 慧晖语音）不在模型训练数据范围内，识别可能出现乱码；真人语音和现代 TTS 语音识别可靠
- 网络延迟会影响体验，建议手机和电脑在同一局域网
- Whisper 引擎在 CPU 上较慢，建议有 NVIDIA GPU（自动使用 CUDA 加速）
