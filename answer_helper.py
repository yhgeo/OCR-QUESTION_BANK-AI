"""
头脑王者答题辅助 - OCR自动识别版
通过截图识别屏幕题目，优先查题库，未命中时由AI自动答题
"""

import base64
import difflib
import json
import re
import time
from io import BytesIO
from pathlib import Path

import requests
import win32gui
from ctypes import windll
from PIL import Image, ImageGrab, ImageOps

try:
    import numpy as np
except Exception:
    np = None

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:
    RapidOCR = None


class BrainKingHelper:
    PROVIDER_PRESETS = [
        ("OpenAI", "https://api.openai.com/v1"),
        ("阿里云百炼(DashScope兼容)", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("DeepSeek", "https://api.deepseek.com"),
        ("OpenRouter", "https://openrouter.ai/api/v1"),
        ("SiliconFlow", "https://api.siliconflow.cn/v1"),
        ("自定义OpenAI兼容接口", None),
    ]
    CONFIG_FILE = Path(__file__).with_name("answer_helper_config.json")
    QUESTION_BANK_FILE = Path(__file__).with_name("answer_question_bank.json")
    QUESTION_BANK_DIR = Path(__file__).with_name("question_banks")
    DEFAULT_ANSWER_PROMPT = (
        "请识别图片中的题目和选项，并直接给出正确答案。"
        "只需要回答选项的具体内容，不要说A、B、C、D这些字母，也不要解释。"
    )
    DEFAULT_EXTRACT_PROMPT = (
        "请把图片中的题目与选项尽量逐字转写成纯文本，不要回答题目。"
        "不要改写，不要概括，不要补充推测。"
        "看不清的字保留原样，实在无法辨认可用？代替。"
        "请严格按这个格式输出：\n"
        "题目：<题干全文>\n"
        "选项A：<内容>\n"
        "选项B：<内容>\n"
        "选项C：<内容>\n"
        "选项D：<内容>"
    )
    QUESTION_BANK_SIMILARITY_THRESHOLD = 0.72
    QUESTION_BANK_SUBSTRING_SIMILARITY_THRESHOLD = 0.80
    MODEL_UNAVAILABLE_ERROR_KEYWORDS = [
        "connection",
        "connect",
        "timed out",
        "timeout",
        "name or service not known",
        "temporary failure",
        "failed to establish a new connection",
        "max retries exceeded",
        "proxyerror",
        "ssl",
        "refused",
        "unreachable",
        "dns",
        "network",
        "read timed out",
        "api key",
        "unauthorized",
        "authentication",
        "401",
        "403",
        "404",
        "429",
        "500",
        "502",
        "503",
        "504",
    ]

    def __init__(self):
        self.provider_name = None
        self.api_key = ""
        self.base_url = ""
        self.model = ""
        self.answer_count = 0
        self.start_time = None
        self.target_window = None
        self.capture_region = None
        self.default_background_prompt = ""
        self.session_background_prompt = ""
        self.question_bank_enabled = False
        self.question_bank_entries = []
        self.model_enabled = False
        self.question_bank_ready = False
        self.question_bank_unavailable_reason = "题库未开启"
        self.offline_ocr_engine = None
        self.offline_ocr_ready = False
        self.offline_ocr_unavailable_reason = "离线OCR未初始化"

        try:
            windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    def normalize_base_url(self, base_url):
        return base_url.strip().rstrip("/")

    def load_saved_config(self):
        try:
            if not self.CONFIG_FILE.exists():
                return None
            with self.CONFIG_FILE.open("r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            print(f"⚠️  读取配置失败: {e}")
            return None

        required_fields = ["provider_name", "api_key", "base_url", "model"]
        if not all(config.get(field) for field in required_fields):
            return None
        return config

    def save_config(self):
        config = {
            "provider_name": self.provider_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "default_background_prompt": self.default_background_prompt,
            "model_enabled": self.model_enabled,
        }
        try:
            with self.CONFIG_FILE.open("w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"✓ 配置已保存到 {self.CONFIG_FILE.name}")
        except Exception as e:
            print(f"⚠️  保存配置失败: {e}")

    def apply_config(self, config):
        self.provider_name = config["provider_name"]
        self.api_key = config["api_key"]
        self.base_url = self.normalize_base_url(config["base_url"])
        self.model = config["model"]
        self.default_background_prompt = str(config.get("default_background_prompt", "") or "").strip()
        self.question_bank_enabled = False
        self.model_enabled = bool(config.get("model_enabled", True))

    def reset_model_config(self):
        self.provider_name = None
        self.api_key = ""
        self.base_url = ""
        self.model = ""
        self.model_enabled = False

    def show_current_config(self):
        if not self.model_enabled:
            print("\n当前模型配置: 未启用")
            return

        print("\n当前模型配置:")
        print(f"   • 提供方: {self.provider_name}")
        print(f"   • Base URL: {self.base_url}")
        print(f"   • 模型: {self.model}")
        if self.default_background_prompt:
            print(f"   • 默认答题背景: {self.default_background_prompt}")

    def has_model_config(self):
        return all([
            self.provider_name,
            self.api_key,
            self.base_url,
            self.model,
        ])

    def is_model_available_error(self, error_message):
        message = str(error_message or "").strip().lower()
        if not message:
            return False
        return any(keyword in message for keyword in self.MODEL_UNAVAILABLE_ERROR_KEYWORDS)

    def is_model_ready(self):
        return self.model_enabled and self.has_model_config()

    def update_question_bank_status(self, ready, reason):
        self.question_bank_ready = ready
        self.question_bank_unavailable_reason = reason

    def format_error_message(self, error):
        if error is None:
            return "未知错误"
        if isinstance(error, str):
            text = error.strip()
        else:
            text = repr(error).strip()

        if not text:
            return "未知错误"
        text = text.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text)
        if len(text) > 300:
            text = text[:300] + "..."
        return text

    def build_url(self, path):
        return f"{self.base_url}/{path.lstrip('/')}"

    def build_headers(self, include_json=False):
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        if self.provider_name == "OpenRouter":
            headers["HTTP-Referer"] = "https://localhost"
            headers["X-Title"] = "BrainKingHelper"
        return headers

    def fetch_models(self):
        if not self.base_url or not self.api_key:
            return None, "模型配置不完整，无法拉取模型列表"

        try:
            response = requests.get(
                self.build_url("models"),
                headers=self.build_headers(),
                timeout=20
            )
        except Exception as e:
            return None, f"请求模型列表失败: {e}"

        if response.status_code != 200:
            try:
                error_text = response.json()
            except Exception:
                error_text = response.text
            return None, f"获取模型列表失败 ({response.status_code}): {error_text}"

        try:
            result = response.json()
            data = result.get("data", [])
            models = [item.get("id", "").strip() for item in data if item.get("id")]
            models = list(dict.fromkeys(models))
            if not models:
                return None, "模型列表为空，当前提供方可能不支持 /models 接口"
            return models, None
        except Exception as e:
            return None, f"解析模型列表失败: {e}"

    def choose_model(self, models):
        all_models = list(models)

        while True:
            keyword = input("输入模型关键词过滤（回车显示全部/前50项）: ").strip().lower()
            filtered = [m for m in all_models if keyword in m.lower()] if keyword else all_models

            if not filtered:
                print("✗ 没有匹配的模型，请换个关键词\n")
                continue

            shown = filtered[:50]
            print(f"\n可选模型 ({len(filtered)} 个，当前展示 {len(shown)} 个):")
            for i, model_name in enumerate(shown, 1):
                print(f"{i}. {model_name}")
            if len(filtered) > len(shown):
                print("...模型较多，建议输入关键词继续筛选，或直接输入完整模型ID")

            model_input = input("\n输入模型序号，或直接输入完整模型ID: ").strip()
            if not model_input:
                print("✗ 请输入模型序号或模型ID\n")
                continue

            if model_input.isdigit():
                index = int(model_input)
                if 1 <= index <= len(shown):
                    return shown[index - 1]
                print("✗ 序号超出范围\n")
                continue

            if model_input in all_models:
                return model_input

            print("✗ 模型输入无效，请重新选择\n")

    def configure_model_provider(self):
        saved_config = self.load_saved_config()
        if saved_config:
            self.apply_config(saved_config)
            print("\n检测到已保存的大模型配置。")
            self.show_current_config()
            use_saved = input("是否使用已保存的大模型配置? (Y/n): ").strip().lower()
            if use_saved in ['', 'y', 'yes']:
                self.model_enabled = True
                print("✓ 已自动加载配置文件中的大模型配置")
                return True

        self.reset_model_config()
        print("\n" + "=" * 70)
        print("模型配置")
        print("支持 OpenAI 兼容接口，模型列表可拉取，也可手动输入模型ID")
        print("=" * 70)

        while True:
            print("\n可选模型提供方:")
            for i, (name, _) in enumerate(self.PROVIDER_PRESETS, 1):
                print(f"{i}. {name}")

            provider_input = input("\n请选择模型提供方序号: ").strip().lower()
            if not provider_input.isdigit():
                print("✗ 请输入数字序号")
                continue

            provider_index = int(provider_input)
            if not 1 <= provider_index <= len(self.PROVIDER_PRESETS):
                print("✗ 序号超出范围")
                continue

            provider_name, default_base_url = self.PROVIDER_PRESETS[provider_index - 1]
            self.provider_name = provider_name

            if default_base_url:
                custom_base = input(
                    f"API Base URL（回车使用默认 {default_base_url}）: "
                ).strip()
                self.base_url = self.normalize_base_url(custom_base or default_base_url)
            else:
                while True:
                    custom_base = input("请输入 API Base URL（例如 https://example.com/v1）: ").strip()
                    if custom_base:
                        self.base_url = self.normalize_base_url(custom_base)
                        break
                    print("✗ API Base URL 不能为空")

            while True:
                api_key = input("请输入 API Key: ").strip()
                if api_key:
                    self.api_key = api_key
                    break
                print("✗ API Key 不能为空")

            print("\n正在拉取模型列表...")
            models, error = self.fetch_models()
            if error:
                print(f"⚠️  {error}")
                manual_model = input("当前无法拉取模型列表，是否手动输入模型ID? (Y/n): ").strip().lower()
                if manual_model in ['', 'y', 'yes']:
                    while True:
                        model_input = input("请输入完整模型ID: ").strip()
                        if model_input:
                            self.model = model_input
                            self.model_enabled = True
                            self.save_config()
                            print("\n✓ 模型配置完成（手动输入模型ID）")
                            self.show_current_config()
                            return True
                        print("✗ 模型ID 不能为空")

                retry = input("输入 r 重试，p 重新选择提供方，q 取消配置: ").strip().lower()
                if retry == 'q':
                    self.reset_model_config()
                    print("✓ 已取消模型配置")
                    return False
                if retry == 'p':
                    continue
                continue

            self.model = self.choose_model(models)
            self.model_enabled = True
            self.save_config()
            print("\n✓ 模型配置完成")
            self.show_current_config()
            return True

    def configure_answer_background(self):
        print("\n" + "=" * 70)
        print("额外答题背景设置")
        print("=" * 70)

        self.session_background_prompt = ""
        default_prompt = self.default_background_prompt.strip()

        if default_prompt:
            print(f"当前默认背景提示词: {default_prompt}")
            choice = input("是否使用默认背景提示词? (Y=使用 / n=不使用 / e=覆盖本次): ").strip().lower()
            if choice in ['', 'y', 'yes']:
                self.session_background_prompt = default_prompt
                print("✓ 本次将使用默认背景提示词")
                return
            if choice == 'e':
                custom_prompt = input("请输入本次答题背景提示词: ").strip()
                self.session_background_prompt = custom_prompt
                if custom_prompt:
                    save_choice = input("是否将本次内容保存为新的默认背景提示词? (y/N): ").strip().lower()
                    if save_choice in ['y', 'yes']:
                        self.default_background_prompt = custom_prompt
                        self.save_config()
                return

            print("✓ 本次不使用额外答题背景")
            edit_default = input("是否修改默认背景提示词? (y/N): ").strip().lower()
            if edit_default in ['y', 'yes']:
                new_default = input("请输入新的默认背景提示词（留空表示清除默认值）: ").strip()
                self.default_background_prompt = new_default
                self.save_config()
            return

        choice = input("是否为本次答题添加额外背景提示词? (y/N): ").strip().lower()
        if choice not in ['y', 'yes']:
            print("✓ 本次不使用额外答题背景")
            return

        custom_prompt = input("请输入本次答题背景提示词: ").strip()
        self.session_background_prompt = custom_prompt
        if not custom_prompt:
            print("✓ 未输入内容，本次不使用额外答题背景")
            return

        save_choice = input("是否保存为默认背景提示词? (y/N): ").strip().lower()
        if save_choice in ['y', 'yes']:
            self.default_background_prompt = custom_prompt
            self.save_config()

    def initialize_offline_ocr(self):
        if RapidOCR is None:
            self.offline_ocr_ready = False
            self.offline_ocr_unavailable_reason = "未安装 rapidocr-onnxruntime"
            return False
        if np is None:
            self.offline_ocr_ready = False
            self.offline_ocr_unavailable_reason = "未安装 numpy"
            return False

        try:
            self.offline_ocr_engine = RapidOCR()
            self.offline_ocr_ready = True
            self.offline_ocr_unavailable_reason = ""
            return True
        except Exception as e:
            self.offline_ocr_engine = None
            self.offline_ocr_ready = False
            self.offline_ocr_unavailable_reason = f"离线OCR初始化失败: {e}"
            return False

    def prepare_ocr_image(self, image):
        grayscale = ImageOps.grayscale(image)
        enlarged = grayscale.resize((grayscale.width * 2, grayscale.height * 2), Image.Resampling.LANCZOS)
        enhanced = ImageOps.autocontrast(enlarged)
        thresholded = enhanced.point(lambda pixel: 255 if pixel > 180 else 0)
        return thresholded

    def extract_question_text_offline(self, image):
        if not self.offline_ocr_ready or self.offline_ocr_engine is None:
            return None, self.offline_ocr_unavailable_reason

        try:
            prepared_image = self.prepare_ocr_image(image)
            image_array = np.array(prepared_image)
            result, _ = self.offline_ocr_engine(image_array)
        except Exception as e:
            return None, f"离线OCR识题失败: {e}"

        if not result:
            return None, "离线OCR未识别到文本"

        lines = []
        for item in result:
            if not item or len(item) < 2:
                continue
            text = str(item[1] or "").strip()
            if text:
                lines.append(text)

        if not lines:
            return None, "离线OCR未提取到有效文本"

        return self.clean_extracted_question_text("\n".join(lines)), None

    def load_question_bank_entries_from_data(self, data):
        entries = data.get("entries", [])
        valid_entries = []
        for item in entries:
            question = str(item.get("question", "") or "").strip()
            answer = str(item.get("answer", "") or "").strip()
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []
            aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]
            if question and answer:
                valid_entries.append({
                    "question": question,
                    "answer": answer,
                    "aliases": aliases,
                })
        return valid_entries

    def load_question_bank(self):
        valid_entries = []
        loaded_sources = []
        errors = []

        if self.QUESTION_BANK_DIR.exists() and self.QUESTION_BANK_DIR.is_dir():
            for json_file in sorted(self.QUESTION_BANK_DIR.glob("*.json")):
                try:
                    with json_file.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    file_entries = self.load_question_bank_entries_from_data(data)
                    valid_entries.extend(file_entries)
                    loaded_sources.append(f"{json_file.name}({len(file_entries)}条)")
                except Exception as e:
                    errors.append(f"{json_file.name}: {e}")

        if self.QUESTION_BANK_FILE.exists():
            try:
                with self.QUESTION_BANK_FILE.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                file_entries = self.load_question_bank_entries_from_data(data)
                valid_entries.extend(file_entries)
                loaded_sources.append(f"{self.QUESTION_BANK_FILE.name}({len(file_entries)}条)")
            except Exception as e:
                errors.append(f"{self.QUESTION_BANK_FILE.name}: {e}")

        if not valid_entries:
            if errors:
                return [], "读取题库失败: " + "；".join(errors)
            return [], (
                f"未找到可用题库，请将 JSON 题库放入 {self.QUESTION_BANK_DIR.name} 文件夹，"
                f"或维护 {self.QUESTION_BANK_FILE.name}"
            )

        return valid_entries, None, loaded_sources

    def configure_question_bank(self, auto_enable=False):
        print("\n" + "=" * 70)
        print("题库设置")
        print("=" * 70)

        if auto_enable:
            self.question_bank_enabled = True
            print("✓ 已自动启用题库")
        else:
            choice = input("是否开启题库优先? (y/N): ").strip().lower()
            self.question_bank_enabled = choice in ['y', 'yes']

        if not self.question_bank_enabled:
            self.question_bank_entries = []
            self.update_question_bank_status(False, "题库未开启")
            print("✓ 本次已关闭题库")
            return

        entries, error, sources = self.load_question_bank()
        if error:
            self.question_bank_entries = []
            self.update_question_bank_status(False, error)
            print(f"⚠️  {error}")
            print("⚠️  当前题库不可用")
            return

        self.question_bank_entries = entries
        if not entries:
            self.update_question_bank_status(False, "题库为空")
            print("⚠️  题库为空，当前题库不可用")
            self.question_bank_enabled = False
            return

        if not self.initialize_offline_ocr():
            self.update_question_bank_status(False, self.offline_ocr_unavailable_reason)
            print(f"⚠️  题库已加载 {len(entries)} 条，但当前不可用: {self.offline_ocr_unavailable_reason}")
            return

        self.update_question_bank_status(True, "")
        print(f"✓ 题库已开启，已加载 {len(entries)} 条题目")
        print("✓ 离线OCR已就绪，题库可独立识题")
        if sources:
            print("   • 来源: " + "，".join(sources))

    def build_text_prompt(self, purpose):
        if purpose == "extract":
            return self.DEFAULT_EXTRACT_PROMPT

        background = self.session_background_prompt.strip()
        base_prompt = self.DEFAULT_ANSWER_PROMPT
        if not background:
            return base_prompt

        return f"{base_prompt}\n\n补充答题背景信息：\n{background}"

    def extract_answer_text(self, result):
        try:
            content = result["choices"][0]["message"]["content"]
        except Exception:
            content = result.get("output_text")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict):
                    text_value = item.get("text")
                    if isinstance(text_value, str):
                        texts.append(text_value)
                    elif isinstance(text_value, dict) and isinstance(text_value.get("value"), str):
                        texts.append(text_value["value"])
            return "\n".join(t.strip() for t in texts if t and t.strip())

        return None

    def find_window(self, window_title=None, return_list=False):
        def callback(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    windows.append((hwnd, title))

        windows = []
        win32gui.EnumWindows(callback, windows)

        if return_list:
            return windows[:20]

        if window_title:
            for hwnd, title in windows:
                if window_title.lower() in title.lower():
                    return hwnd

        return None

    def select_window_by_index(self, index):
        windows = self.find_window(return_list=True)
        if 1 <= index <= len(windows):
            return windows[index - 1][0]
        return None

    def get_window_rect(self, hwnd):
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)

            if left < -10000 or top < -10000:
                print("⚠️  窗口可能最小化或隐藏")
                return None

            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                print("⚠️  窗口大小无效")
                return None

            return (left, top, right, bottom)
        except Exception as e:
            print(f"获取窗口位置失败: {e}")
            return None

    def capture_screen(self, use_region=False):
        try:
            if use_region and self.capture_region:
                screenshot = ImageGrab.grab(bbox=self.capture_region)
            elif self.target_window:
                rect = self.get_window_rect(self.target_window)
                if rect:
                    screenshot = ImageGrab.grab(bbox=rect)
                else:
                    screenshot = ImageGrab.grab()
            else:
                screenshot = ImageGrab.grab()
            return screenshot
        except Exception as e:
            print(f"截图失败: {e}")
            return None

    def save_preview_image(self, image, filename):
        save_path = Path(__file__).with_name(filename)
        try:
            image.save(save_path)
            return save_path
        except Exception:
            return None

    def image_to_base64(self, image):
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    def request_multimodal_text(self, image, prompt, max_tokens=300):
        if not self.is_model_ready():
            return None, "模型未配置或当前未启用"

        try:
            img_base64 = self.image_to_base64(image)
            data = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}"
                                }
                            }
                        ]
                    }
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }

            response = requests.post(
                self.build_url("chat/completions"),
                headers=self.build_headers(include_json=True),
                json=data,
                timeout=30
            )

            if response.status_code != 200:
                try:
                    error_text = response.json()
                except Exception:
                    error_text = response.text
                return None, f"请求失败 ({response.status_code}): {error_text}"

            result = response.json()
            text = self.extract_answer_text(result)
            if not text:
                return None, "模型返回为空"
            return text.strip(), None
        except Exception as e:
            return None, str(e)

    def extract_question_text(self, image):
        prompt = self.build_text_prompt("extract")
        text, error = self.request_multimodal_text(image, prompt, max_tokens=500)
        if error or not text:
            return text, error
        return self.clean_extracted_question_text(text), None

    def answer_question(self, image):
        prompt = self.build_text_prompt("answer")
        return self.request_multimodal_text(image, prompt, max_tokens=200)

    def clean_extracted_question_text(self, text):
        cleaned_lines = []
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^(题目|问题)\s*[：:]\s*", "", line)
            line = re.sub(r"^(选项\s*[A-DＡ-Ｄ]|[A-DＡ-Ｄ])[\s：:.、]*", "", line)
            cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    def normalize_question_text(self, text):
        normalized = str(text or "").lower()
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[，。！？：；、“”‘’（）()【】\[\]《》<>·.,!?;:'\"\\/_=+\-]", "", normalized)
        return normalized

    def calculate_text_similarity(self, left, right):
        return difflib.SequenceMatcher(None, left, right).ratio()

    def calculate_best_substring_similarity(self, left, right):
        if not left or not right:
            return 0.0

        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if shorter in longer:
            return 1.0

        window = len(shorter)
        if len(longer) <= window:
            return self.calculate_text_similarity(shorter, longer)

        best_score = 0.0
        for start in range(0, len(longer) - window + 1):
            candidate = longer[start:start + window]
            score = self.calculate_text_similarity(shorter, candidate)
            if score > best_score:
                best_score = score
        return best_score

    def find_answer_in_question_bank(self, question_text):
        normalized_question = self.normalize_question_text(question_text)
        if not normalized_question:
            return None, None

        best_contains_match = None
        best_contains_length = -1
        best_similarity_match = None
        best_similarity_score = 0.0
        best_similarity_candidate = ""
        best_substring_match = None
        best_substring_score = 0.0
        best_substring_candidate = ""

        for entry in self.question_bank_entries:
            candidates = [entry["question"], *entry.get("aliases", [])]
            for candidate in candidates:
                normalized_candidate = self.normalize_question_text(candidate)
                if not normalized_candidate:
                    continue

                if (
                    normalized_candidate in normalized_question
                    or normalized_question in normalized_candidate
                ):
                    candidate_length = len(normalized_candidate)
                    if candidate_length > best_contains_length:
                        best_contains_match = entry
                        best_contains_length = candidate_length

                similarity = self.calculate_text_similarity(normalized_question, normalized_candidate)
                if similarity > best_similarity_score:
                    best_similarity_score = similarity
                    best_similarity_match = entry
                    best_similarity_candidate = candidate

                substring_similarity = self.calculate_best_substring_similarity(
                    normalized_question,
                    normalized_candidate,
                )
                if substring_similarity > best_substring_score:
                    best_substring_score = substring_similarity
                    best_substring_match = entry
                    best_substring_candidate = candidate

        if best_contains_match:
            return best_contains_match, {
                "mode": "contains",
                "normalized_question_length": len(normalized_question),
            }

        if best_substring_match and best_substring_score >= self.QUESTION_BANK_SUBSTRING_SIMILARITY_THRESHOLD:
            return best_substring_match, {
                "mode": "substring_similarity",
                "score": best_substring_score,
                "candidate": best_substring_candidate,
                "normalized_question_length": len(normalized_question),
            }

        if best_similarity_match and best_similarity_score >= self.QUESTION_BANK_SIMILARITY_THRESHOLD:
            return best_similarity_match, {
                "mode": "similarity",
                "score": best_similarity_score,
                "candidate": best_similarity_candidate,
                "normalized_question_length": len(normalized_question),
            }

        best_score = best_similarity_score
        best_candidate = best_similarity_candidate
        best_mode = "miss"
        if best_substring_score > best_score:
            best_score = best_substring_score
            best_candidate = best_substring_candidate
            best_mode = "substring_miss"

        best_entry = best_similarity_match
        if best_substring_score > best_similarity_score:
            best_entry = best_substring_match

        return None, {
            "mode": best_mode,
            "score": best_score,
            "candidate": best_candidate,
            "candidate_answer": best_entry["answer"] if best_entry else "",
            "normalized_question_length": len(normalized_question),
        }


def main():
    helper = BrainKingHelper()
    helper.start_time = time.time()

    print("=" * 70)
    print("      🧠 智能答题助手 - OCR自动识别版")
    print("=" * 70)
    print("\n⚡ 自动识别模式")
    print("📸 按回车键截图识别题目并获取答案")
    print("💡 输入 q 退出，w 设置窗口\n")
    print("=" * 70)

    saved_config = helper.load_saved_config()
    default_model_choice = 'Y/n' if saved_config else 'y/N'
    use_model_choice = input(f"是否启用大模型答题? ({default_model_choice}): ").strip().lower()
    use_model = bool(saved_config) if use_model_choice == '' else use_model_choice in ['y', 'yes']

    if use_model:
        helper.configure_model_provider()
        if helper.is_model_ready():
            helper.configure_answer_background()
        else:
            print("\n⚠️  当前未完成模型配置，AI答题不可用")
        helper.configure_question_bank(auto_enable=False)
    else:
        helper.reset_model_config()
        print("\n✓ 本次已跳过大模型配置和使用")
        helper.configure_question_bank(auto_enable=True)

    if not helper.question_bank_ready and not helper.is_model_ready():
        print("\n❌ 当前既无法使用题库，也无法使用模型答题，程序已退出。")
        print(f"   • 题库状态: {helper.question_bank_unavailable_reason}")
        return

    print("\n当前能力状态:")
    print(f"   • 题库: {'可用' if helper.question_bank_ready else '不可用'}")
    if not helper.question_bank_ready:
        print(f"     - 原因: {helper.question_bank_unavailable_reason}")
    print(f"   • AI答题: {'可用' if helper.is_model_ready() else '不可用'}")

    print()
    setup = input("是否设置固定窗口? (y/n，回车跳过): ").strip().lower()
    if setup == 'y':
        windows = helper.find_window(return_list=True)
        print("\n可用窗口列表:")
        for i, (hwnd, title) in enumerate(windows, 1):
            print(f"{i}. {title}")

        window_input = input("\n输入序号或窗口名称关键词: ").strip()
        hwnd = None

        if window_input.isdigit():
            index = int(window_input)
            hwnd = helper.select_window_by_index(index)
            if hwnd:
                window_title = windows[index - 1][1]
                print(f"✓ 已选择: {window_title}")
        else:
            hwnd = helper.find_window(window_input)
            if hwnd:
                print("✓ 已找到窗口")

        if hwnd:
            helper.target_window = hwnd
            rect = helper.get_window_rect(hwnd)
            if rect:
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                print(f"✓ 窗口区域: 位置({rect[0]}, {rect[1]}) 大小({width}x{height})")

                print("\n正在截取窗口预览...")
                preview = ImageGrab.grab(bbox=rect)
                preview_path = helper.save_preview_image(preview, "window_preview.png")
                if preview_path:
                    print("✓ 预览已保存为 window_preview.png，请查看确认窗口内容")
                else:
                    print("⚠️  预览图保存失败，已跳过保存")
            else:
                print("⚠️  无法获取窗口位置，可能窗口被最小化")
                helper.target_window = None

            if rect:
                set_region = input("\n是否设置截图区域? (y/n，回车跳过): ").strip().lower()
                if set_region == 'y':
                    print("\n提示：输入相对于窗口的坐标 (左,上,右,下)")
                    print(f"窗口大小: {width}x{height}")
                    print("例如：0,100,326,400 (从窗口左上角开始)")
                    print("或直接输入: 0,0,{},{}（使用完整窗口）".format(width, height))
                    region_input = input("输入区域: ").strip()
                    try:
                        coords = [int(x.strip()) for x in region_input.split(',')]
                        if len(coords) == 4:
                            left, top, right, bottom = coords
                            helper.capture_region = (
                                rect[0] + left,
                                rect[1] + top,
                                rect[0] + right,
                                rect[1] + bottom
                            )
                            print("✓ 已设置截图区域")

                            region_preview = ImageGrab.grab(bbox=helper.capture_region)
                            region_preview_path = helper.save_preview_image(region_preview, "region_preview.png")
                            if region_preview_path:
                                print("✓ 区域预览已保存为 region_preview.png")
                            else:
                                print("⚠️  区域预览保存失败，已跳过保存")
                    except Exception:
                        print("✗ 区域格式错误，使用完整窗口")
        else:
            print("✗ 未找到窗口")

    print("\n准备就绪！打开头脑王者，看到题目后按回车...\n")

    while True:
        try:
            cmd = input("按回车截图识别 (q退出/w设置窗口): ").strip().lower()

            if cmd == 'q' or cmd in ['quit', 'exit']:
                total_time = time.time() - helper.start_time
                print(f"\n{'=' * 70}")
                print("📊 本轮统计:")
                print(f"   • 答题数量: {helper.answer_count} 题")
                print(f"   • 总用时: {total_time:.1f} 秒")
                if helper.answer_count > 0:
                    print(f"   • 平均速度: {total_time / helper.answer_count:.2f} 秒/题")
                print(f"{'=' * 70}")
                print("\n👋 再见! 下次继续加油!")
                break

            if cmd == 'w':
                windows = helper.find_window(return_list=True)
                print("\n可用窗口列表:")
                for i, (hwnd, title) in enumerate(windows, 1):
                    print(f"{i}. {title}")

                window_input = input("\n输入序号或窗口名称关键词: ").strip()
                hwnd = None

                if window_input.isdigit():
                    index = int(window_input)
                    hwnd = helper.select_window_by_index(index)
                else:
                    hwnd = helper.find_window(window_input)

                if hwnd:
                    helper.target_window = hwnd
                    print("✓ 窗口已更新")
                else:
                    print("✗ 未找到窗口")
                continue

            print("\n📸 正在截图...")
            screenshot = helper.capture_screen(use_region=bool(helper.capture_region))

            if not screenshot:
                print("❌ 截图失败，请检查窗口是否最小化\n")
                continue

            extracted_question = None
            if helper.question_bank_ready:
                print("📚 正在使用离线OCR识题并查询题库...")
                extract_start = time.time()
                extracted_question, extract_error = helper.extract_question_text_offline(screenshot)
                extract_elapsed = time.time() - extract_start

                if extract_error:
                    print(f"⚠️  题库识题失败: {extract_error}")
                elif extracted_question:
                    matched_entry, match_debug = helper.find_answer_in_question_bank(extracted_question)
                    if matched_entry:
                        helper.answer_count += 1
                        print(f"\n{'=' * 60}")
                        print(f"✅ 题库答案: {matched_entry['answer']}")
                        print(f"📝 识别题目: {extracted_question}")
                        if match_debug and match_debug.get("mode") in ["similarity", "substring_similarity"]:
                            mode_label = "子串相似度命中" if match_debug.get("mode") == "substring_similarity" else "相似度命中"
                            print(f"🎯 {mode_label}: {match_debug['score']:.2f}（匹配题目: {match_debug['candidate']}）")
                        print(f"⚡ 识题用时: {extract_elapsed:.2f}秒")
                        print(f"{'=' * 60}\n")
                        continue
                    print("⚠️  题库未命中")
                    if match_debug:
                        print(
                            f"   • 规范化题目长度: {match_debug.get('normalized_question_length', 0)}"
                            f"，最高相似度: {match_debug.get('score', 0):.2f}"
                        )
                        candidate = match_debug.get("candidate")
                        if candidate:
                            print(f"   • 最接近题库题目: {candidate}")
                        candidate_answer = match_debug.get("candidate_answer")
                        if candidate_answer:
                            print(f"   • 对应题库答案: {candidate_answer}")
            elif helper.question_bank_enabled:
                print(f"⚠️  当前题库不可用: {helper.question_bank_unavailable_reason}")

            if not helper.is_model_ready():
                print("❌ 题库未命中，且当前模型不可用，无法继续AI答题。\n")
                continue

            print("🔍 AI识别并答题中...")
            start = time.time()
            answer, error = helper.answer_question(screenshot)
            elapsed = time.time() - start

            if error or not answer:
                formatted_error = helper.format_error_message(error or '模型未返回答案')
                if helper.is_model_available_error(formatted_error):
                    print(f"❌ 模型当前不可用，已停止本次AI重试: {formatted_error}\n")
                else:
                    print(f"❌ AI答题失败，请重试: {formatted_error}\n")
                continue

            helper.answer_count += 1
            print(f"\n{'=' * 60}")
            print(f"✅ AI答案: {answer}")
            if extracted_question:
                print(f"📝 识别题目: {extracted_question}")
            print(f"⚡ 用时: {elapsed:.2f}秒")
            print(f"{'=' * 60}\n")

        except KeyboardInterrupt:
            print("\n\n程序中断")
            break
        except Exception as e:
            print(f"❌ 错误: {e}\n")


if __name__ == "__main__":
    main()
