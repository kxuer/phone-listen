"""接收手机传来的语音，识别成文字并粘贴到当前光标所在输入框。

用法:
    python listener.py                     # faster-whisper（默认，精度高、带标点）
    python listener.py --model small       # Vosk 小模型（42MB，最快）
    python listener.py --model large       # Vosk 大模型（1.3GB，Vosk 中精度最高）
    python listener.py --model whisper-medium   # 更大的 whisper 模型，精度更高更慢
    python listener.py --model PATH        # 本地 Vosk 模型目录
    python listener.py --play              # 识别的同时播放声音
    python listener.py --save              # 同时保存录音为 wav
    python listener.py --no-type           # 只识别并打印，不粘贴（测试用）
    python listener.py --compute-type float32  # whisper 推理精度（默认 float16，GPU 更快）

模型按需自动下载：Vosk 模型在 model/ 目录，Whisper 模型在 HuggingFace 缓存目录，
各模型可同时保留、随时切换。
"""

import argparse
import json
import os
import socket
import threading
import time
import urllib.request
import wave
import zipfile
from datetime import datetime
from pathlib import Path

import pyautogui
import pyperclip

SAMPLE_RATE = 16000  # 与手机端一致
CHUNK = 3200  # 100ms 音频
MODEL_ROOT = Path(__file__).parent / "model"

# Vosk 模型预设：别名 -> (目录名, 下载地址, 体积提示)
VOSK_MODELS = {
    "small": (
        "vosk-model-small-cn-0.22",
        "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip",
        "42MB",
    ),
    "large": (
        "vosk-model-cn-0.22",
        "https://alphacephei.com/vosk/models/vosk-model-cn-0.22.zip",
        "1.3GB",
    ),
}

# faster-whisper 可选尺寸：别名 -> (HF 模型名, 本地目录名, 体积提示)
WHISPER_SIZES = {
    "whisper-tiny": ("Systran/faster-whisper-tiny", "faster-whisper-tiny", "~75MB"),
    "whisper-base": ("Systran/faster-whisper-base", "faster-whisper-base", "~145MB"),
    "whisper": ("Systran/faster-whisper-small", "faster-whisper-small", "~484MB"),
    "whisper-small": ("Systran/faster-whisper-small", "faster-whisper-small", "~484MB"),
    "whisper-medium": ("Systran/faster-whisper-medium", "faster-whisper-medium", "~1.5GB"),
    "whisper-large-v3": ("Systran/faster-whisper-large-v3", "faster-whisper-large-v3", "~3GB"),
}


# ----------------------------- Vosk 模型下载 -----------------------------

def _download_resume(url: str, zip_path: Path):
    """支持断点续传的下载，网络中断自动续传直到完成。"""
    existing = zip_path.stat().st_size if zip_path.exists() else 0
    req = urllib.request.Request(url)
    if existing:
        req.add_header("Range", f"bytes={existing}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        if existing and resp.status != 206:
            # 服务器不支持续传，从头下载
            existing = 0
        remote_size = int(resp.headers.get("Content-Length", 0))
        total = remote_size + existing
        mode = "ab" if existing else "wb"
        if existing:
            print(f"从 {existing / 1024 / 1024:.0f}MB 处断点续传...")
        pos = existing
        with open(zip_path, mode) as f:
            while True:
                chunk = resp.read(1 << 18)  # 256KB
                if not chunk:
                    break
                f.write(chunk)
                pos += len(chunk)
                if total:
                    pct = pos * 100 // total
                    print(f"\r下载中 {pos / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f}MB ({pct}%)",
                          end="", flush=True)
    print()


def ensure_vosk_model(spec: str) -> Path:
    """返回 Vosk 模型目录；缺失时自动下载。spec 为别名或本地路径。"""
    as_path = Path(spec)
    if as_path.exists() and (as_path / "am").exists():
        return as_path

    if spec not in VOSK_MODELS:
        raise SystemExit(
            f"未知模型 '{spec}'，可选: whisper, {', '.join(VOSK_MODELS)}，或给出本地模型目录路径"
        )

    name, url, size_hint = VOSK_MODELS[spec]
    model_dir = MODEL_ROOT / name
    if model_dir.exists() and (model_dir / "am").exists():
        return model_dir

    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    zip_path = MODEL_ROOT / f"{name}.zip"
    print(f"首次使用 {spec} 模型，正在下载（约 {size_hint}），支持断点续传...")
    for attempt in range(1, 200):
        try:
            _download_resume(url, zip_path)
            with zipfile.ZipFile(zip_path) as z:
                if z.testzip() is not None:
                    raise zipfile.BadZipFile("压缩包校验失败")
            break
        except Exception as e:
            print(f"\n第 {attempt} 次下载中断（{e}），2 秒后续传...")
            time.sleep(2)
    else:
        raise SystemExit("模型下载失败，请检查网络后重试（已下载部分会保留并续传）")

    print("下载完成，解压中（大模型可能需要一两分钟）...")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(MODEL_ROOT)
    zip_path.unlink()
    if not (model_dir / "am").exists():
        raise SystemExit(f"解压后未找到模型目录 {model_dir}")
    print(f"模型就绪: {model_dir.name}")
    return model_dir


# ----------------------------- faster-whisper -----------------------------

_whisper_model = None


def _whisper_manual_download(model_name: str, local_dir: Path):
    """HuggingFace 客户端走不通时的兜底：从 hf-mirror 多线程分块下载到本地目录。"""
    import concurrent.futures

    base = f"https://hf-mirror.com/{model_name}/resolve/main"
    files = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
    threads = 8
    local_dir.mkdir(parents=True, exist_ok=True)

    def remote_size(url):
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers["Content-Length"])

    def fetch_part(url, start, end, part_path):
        want = end - start + 1
        have = part_path.stat().st_size if part_path.exists() else 0
        if have >= want:
            return
        for _ in range(50):
            try:
                req = urllib.request.Request(
                    url, headers={"Range": f"bytes={start + have}-{end}"}
                )
                with urllib.request.urlopen(req, timeout=60) as r, open(part_path, "ab") as f:
                    while True:
                        b = r.read(1 << 18)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                if have >= want:
                    return
            except Exception:
                time.sleep(2)
        raise RuntimeError(f"分块下载失败: {part_path.name}")

    for name in files:
        dst = local_dir / name
        if dst.exists():
            continue
        url = f"{base}/{name}"
        total = remote_size(url)
        print(f"镜像下载 {name} ({total / 1e6:.1f}MB, {threads} 线程)...")
        bounds = []
        for i in range(threads):
            s = total * i // threads
            e = total * (i + 1) // threads - 1
            if s <= e:
                bounds.append((s, e))
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
            futs = [
                ex.submit(fetch_part, url, s, e, Path(f"{dst}.part{i}"))
                for i, (s, e) in enumerate(bounds)
            ]
            for f in concurrent.futures.as_completed(futs):
                f.result()
        with open(dst, "wb") as out:
            for i in range(len(bounds)):
                with open(f"{dst}.part{i}", "rb") as p:
                    out.write(p.read())
        for i in range(len(bounds)):
            Path(f"{dst}.part{i}").unlink(missing_ok=True)
        if dst.stat().st_size != total:
            raise RuntimeError(f"{name} 大小校验失败")
        print(f"{name} 完成")


def get_whisper_model(spec: str, compute_type: str, device: str = "cuda"):
    """加载（并按需下载）faster-whisper 模型。HuggingFace 不通时自动切国内镜像。"""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    from faster_whisper import WhisperModel

    model_name, local_name, size_hint = WHISPER_SIZES[spec]
    local_dir = MODEL_ROOT / local_name
    if (local_dir / "model.bin").exists():
        print(f"加载本地模型 {local_name} ...")
        _whisper_model = WhisperModel(str(local_dir), device=device, compute_type=compute_type)
        print(f"模型就绪: {local_name} ({device}/{compute_type})")
        return _whisper_model

    print(f"首次使用 {spec}，正在准备模型 {model_name}（约 {size_hint}，自动下载，仅一次）...")

    # 对真实大文件做 256KB 限速探测（小文件能通不代表 CDN 不卡），太慢则走国内镜像
    if "HF_ENDPOINT" not in os.environ:
        probe = f"https://huggingface.co/{model_name}/resolve/main/model.bin"
        try:
            req = urllib.request.Request(probe, headers={"Range": "bytes=0-262143"})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=8) as r:
                got = len(r.read())
            if got < 262144 or time.time() - t0 > 6:
                raise OSError("HuggingFace CDN 过慢或不支持断点")
        except Exception:
            # xet 传输不走 HF_ENDPOINT，走镜像时必须禁用，否则元数据请求会打到官方源
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            print("HuggingFace 文件源不可达，已切换国内镜像 hf-mirror.com")

    try:
        _whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        print(f"HuggingFace 客户端下载失败（{e}），改用镜像多线程直连...")
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        try:
            _whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
        except Exception:
            _whisper_manual_download(model_name, local_dir)
            _whisper_model = WhisperModel(str(local_dir), device=device, compute_type=compute_type)
    print(f"模型就绪: {local_name} ({device}/{compute_type})")
    return _whisper_model


class VoskEngine:
    """每次说话新建一个 KaldiRecognizer。"""

    backend = "vosk"

    def __init__(self, model):
        from vosk import KaldiRecognizer

        self.rec = KaldiRecognizer(model, SAMPLE_RATE)
        self.rec.SetWords(False)

    def feed(self, data: bytes):
        self.rec.AcceptWaveform(data)

    def finish(self) -> str:
        result = json.loads(self.rec.FinalResult())
        return "".join(result.get("text", "").split())


class WhisperEngine:
    """faster-whisper 为整段转写：按住时攒 PCM，松开后一次性识别。"""

    backend = "whisper"

    def __init__(self, model):
        import numpy as np

        self._np = np
        self.model = model
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf.extend(data)

    def finish(self) -> str:
        pcm = self._np.frombuffer(bytes(self.buf), dtype=self._np.int16)
        if pcm.size < SAMPLE_RATE // 4:  # 短于 0.25 秒直接丢弃
            return ""
        audio = pcm.astype(self._np.float32) / 32768.0
        print(f"    whisper 转写中（{len(pcm) / SAMPLE_RATE:.1f}s 语音）...")
        segments, _info = self.model.transcribe(
            audio,
            language="zh",
            beam_size=5,
            vad_filter=True,
            initial_prompt="以下是普通话句子，请输出简体中文并加标点。",
        )
        return "".join(seg.text.strip() for seg in segments)


def build_engine(spec: str, compute_type: str):
    """根据 --model 参数返回 (engine工厂, 显示名)。"""
    if spec in WHISPER_SIZES:
        model = get_whisper_model(spec, compute_type)
        return (lambda: WhisperEngine(model)), spec
    model_dir = ensure_vosk_model(spec)
    from vosk import SetLogLevel

    SetLogLevel(-1)
    from vosk import Model

    key = str(model_dir)
    if key not in _vosk_models:
        print(f"加载模型 {model_dir.name} ...")
        _vosk_models[key] = Model(key)
    return (lambda: VoskEngine(_vosk_models[key])), model_dir.name


_vosk_models = {}


# ----------------------------- 输入与服务 -----------------------------

def type_text(text: str):
    """把文字粘贴到当前光标所在输入框（剪贴板 + Ctrl+V，支持中文）。"""
    old = pyperclip.paste()
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    # 恢复剪贴板延迟一拍，避免粘贴动作读到旧值
    threading.Timer(1.0, lambda: pyperclip.copy(old)).start()


def handle(conn: socket.socket, addr, make_engine, opts):
    print(f"[+] {addr[0]} 按下按钮，开始说话")
    engine = make_engine()
    player = None
    if opts.play:
        import sounddevice as sd

        player = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        player.start()

    chunks = []  # 保存 wav 用
    total = 0
    try:
        while True:
            data = conn.recv(CHUNK)
            if not data:
                break
            total += len(data)
            if opts.save:
                chunks.append(data)
            if player is not None:
                player.write(data)  # 直接写 PCM 字节，缓冲满时阻塞限速
            engine.feed(data)
    except (ConnectionResetError, OSError):
        pass
    finally:
        if player is not None:
            player.stop()
            player.close()
        conn.close()

        seconds = total / 2 / SAMPLE_RATE
        try:
            text = engine.finish().strip()
        except Exception as e:
            print(f"[!] {addr[0]} 识别失败: {e}")
            return

        if opts.save and chunks:
            save_dir = Path(__file__).parent / "recordings"
            save_dir.mkdir(exist_ok=True)
            name = save_dir / datetime.now().strftime("%Y%m%d_%H%M%S.wav")
            with wave.open(str(name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(b"".join(chunks))

        if not text:
            print(f"[-] {addr[0]} 松开按钮 ({seconds:.1f}s)，未识别到语音")
            return

        print(f"[-] {addr[0]} 识别结果: {text}")

        if opts.no_type:
            return
        # 稍等片刻让焦点稳定，再粘贴到光标处
        threading.Timer(0.15, type_text, args=(text,)).start()


def main():
    ap = argparse.ArgumentParser(description="接收手机语音 -> 识别 -> 输入到光标处")
    ap.add_argument("--port", type=int, default=8989)
    ap.add_argument("--play", action="store_true", help="识别的同时播放声音")
    ap.add_argument("--save", action="store_true", help="每次说话保存为 wav 文件")
    ap.add_argument("--no-type", action="store_true", help="只识别打印，不粘贴")
    ap.add_argument(
        "--model",
        default="whisper",
        help="模型: whisper(默认) / whisper-medium / whisper-large-v3 / small(vosk) / large(vosk) / 本地Vosk目录",
    )
    ap.add_argument(
        "--compute-type",
        default="float16",
        help="whisper 推理精度: float16(默认,GPU快) / int8(CPU快) / int8_float32 / float32(更准更慢)",
    )
    ap.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return

    make_engine, model_label = build_engine(args.model, args.compute_type)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(5)
    mode = "不粘贴" if args.no_type else "识别后粘贴到光标处"
    print(f"语音识别就绪 | 模型: {model_label} | 模式: {mode} | 监听 0.0.0.0:{args.port}")
    print("在手机 App 里填本机 IP，按住按钮说话，松开即输入 (Ctrl+C 退出)")

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, addr, make_engine, args), daemon=True).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出")
