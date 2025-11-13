"""
头脑王者答题辅助 - OCR自动识别版
通过截图识别屏幕题目，AI自动答题
"""

import requests
import json
import time
from PIL import ImageGrab
from io import BytesIO
import base64
import win32gui
import win32con
import win32api
from ctypes import windll

class BrainKingHelper:
    def __init__(self):
        # 通义千问API配置
        self.api_key = ""
        self.api_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        self.ocr_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        self.answer_count = 0
        self.start_time = None
        self.target_window = None
        self.capture_region = None
        
        # 设置DPI感知，解决Windows缩放问题
        try:
            windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except:
            try:
                windll.user32.SetProcessDPIAware()
            except:
                pass
    
    def find_window(self, window_title=None, return_list=False):
        """查找窗口"""
        def callback(hwnd, windows):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    windows.append((hwnd, title))
        
        windows = []
        win32gui.EnumWindows(callback, windows)
        
        if return_list:
            # 返回窗口列表
            return windows[:20]
        
        if window_title:
            # 按关键词搜索
            for hwnd, title in windows:
                if window_title.lower() in title.lower():
                    return hwnd
        
        return None
    
    def select_window_by_index(self, index):
        """通过序号选择窗口"""
        windows = self.find_window(return_list=True)
        if 1 <= index <= len(windows):
            return windows[index - 1][0]
        return None
    
    def get_window_rect(self, hwnd):
        """获取窗口位置和大小"""
        try:
            # 获取窗口矩形区域
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            
            # 检查坐标是否有效
            if left < -10000 or top < -10000:
                print(f"⚠️  窗口可能最小化或隐藏")
                return None
            
            # 检查窗口大小
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                print(f"⚠️  窗口大小无效")
                return None
            
            return (left, top, right, bottom)
        except Exception as e:
            print(f"获取窗口位置失败: {e}")
            return None
    
    def capture_screen(self, use_region=False):
        """截取屏幕"""
        try:
            if use_region and self.capture_region:
                # 截取指定区域
                screenshot = ImageGrab.grab(bbox=self.capture_region)
            elif self.target_window:
                # 截取指定窗口
                rect = self.get_window_rect(self.target_window)
                if rect:
                    screenshot = ImageGrab.grab(bbox=rect)
                else:
                    screenshot = ImageGrab.grab()
            else:
                # 截取整个屏幕
                screenshot = ImageGrab.grab()
            return screenshot
        except Exception as e:
            print(f"截图失败: {e}")
            return None
    
    def image_to_base64(self, image):
        """将图片转换为base64"""
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()
    
    def ocr_image(self, image):
        """通过通义千问OCR识别图片中的题目"""
        try:
            img_base64 = self.image_to_base64(image)
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": "qwen-vl-plus",
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "image": f"data:image/png;base64,{img_base64}"
                                },
                                {
                                    "text": "请识别图片中的题目和选项，只输出题目内容，不要其他说明。"
                                }
                            ]
                        }
                    ]
                }
            }
            
            response = requests.post(self.ocr_url, headers=headers, json=data, timeout=15)
            
            if response.status_code == 200:
                result = response.json()
                question = result['output']['choices'][0]['message']['content'][0]['text']
                return question.strip()
            else:
                return None
        except Exception as e:
            print(f"OCR识别失败: {e}")
            return None
    
    def get_answer(self, question):
        """快速获取答案"""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": "qwen-plus",  # 使用plus模型，准确率更高
                "messages": [
                    {
                        "role": "system",
                        "content": "你是智能答题助手。直接给出答案,只说答案本身,不要解释。如果是选择题,直接说选项内容。"
                    },
                    {
                        "role": "user",
                        "content": question
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 30
            }
            
            start = time.time()
            response = requests.post(self.api_url, headers=headers, json=data, timeout=10)
            elapsed = time.time() - start
            
            if response.status_code == 200:
                result = response.json()
                answer = result['choices'][0]['message']['content'].strip()
                self.answer_count += 1
                return {
                    'answer': answer,
                    'time': f"{elapsed:.2f}秒"
                }
            else:
                return None
                
        except Exception as e:
            return None

def main():
    helper = BrainKingHelper()
    helper.start_time = time.time()
    
    print("=" * 70)
    print("      🧠 智能答题助手 - OCR自动识别版")
    print("=" * 70)
    print("\n⚡ 自动识别模式")
    print("📸 按回车键截图识别题目并获取答案")
    print("💡 输入 q 退出，w 设置窗口，r 设置区域\n")
    print("=" * 70 + "\n")
    
    # 询问是否设置窗口
    setup = input("是否设置固定窗口? (y/n，回车跳过): ").strip().lower()
    if setup == 'y':
        # 显示窗口列表
        windows = helper.find_window(return_list=True)
        print("\n可用窗口列表:")
        for i, (hwnd, title) in enumerate(windows, 1):
            print(f"{i}. {title}")
        
        window_input = input("\n输入序号或窗口名称关键词: ").strip()
        hwnd = None
        
        # 判断是序号还是关键词
        if window_input.isdigit():
            # 序号选择
            index = int(window_input)
            hwnd = helper.select_window_by_index(index)
            if hwnd:
                window_title = windows[index - 1][1]
                print(f"✓ 已选择: {window_title}")
        else:
            # 关键词搜索
            hwnd = helper.find_window(window_input)
            if hwnd:
                print(f"✓ 已找到窗口")
        
        if hwnd:
            helper.target_window = hwnd
            rect = helper.get_window_rect(hwnd)
            if rect:
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                print(f"✓ 窗口区域: 位置({rect[0]}, {rect[1]}) 大小({width}x{height})")
                
                # 先截图预览
                print("\n正在截取窗口预览...")
                preview = ImageGrab.grab(bbox=rect)
                preview.save("window_preview.png")
                print("✓ 预览已保存为 window_preview.png，请查看确认窗口内容")
                
            else:
                print("⚠️  无法获取窗口位置，可能窗口被最小化")
                helper.target_window = None
            
            # 询问是否进一步设置区域
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
                            # 转换为绝对坐标
                            left, top, right, bottom = coords
                            helper.capture_region = (
                                rect[0] + left,
                                rect[1] + top,
                                rect[0] + right,
                                rect[1] + bottom
                            )
                            print(f"✓ 已设置截图区域")
                            
                            # 预览裁剪区域
                            region_preview = ImageGrab.grab(bbox=helper.capture_region)
                            region_preview.save("region_preview.png")
                            print("✓ 区域预览已保存为 region_preview.png")
                    except:
                        print("✗ 区域格式错误，使用完整窗口")
        else:
            print("✗ 未找到窗口")
    
    print("\n准备就绪！打开头脑王者，看到题目后按回车...\n")
    
    while True:
        try:
            cmd = input("按回车截图识别 (q退出/w设置窗口): ").strip().lower()
            
            if cmd == 'q' or cmd in ['quit', 'exit']:
                total_time = time.time() - helper.start_time
                print(f"\n{'='*70}")
                print(f"📊 本轮统计:")
                print(f"   • 答题数量: {helper.answer_count} 题")
                print(f"   • 总用时: {total_time:.1f} 秒")
                if helper.answer_count > 0:
                    print(f"   • 平均速度: {total_time/helper.answer_count:.2f} 秒/题")
                print(f"{'='*70}")
                print("\n👋 再见! 下次继续加油!")
                break
            
            if cmd == 'w':
                # 重新设置窗口
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
            
            print("🔍 OCR识别中...")
            question = helper.ocr_image(screenshot)
            
            if not question:
                print("❌ 识别失败，请重试\n")
                continue
            
            print(f"📝 题目: {question}\n")
            print("⏳ AI答题中...")
            
            result = helper.get_answer(question)
            
            if result:
                print(f"\n{'='*60}")
                print(f"✅ 答案: {result['answer']}")
                print(f"⚡ 用时: {result['time']}")
                print(f"{'='*60}\n")
            else:
                print("❌ 获取答案失败\n")
        
        except KeyboardInterrupt:
            print("\n\n程序中断")
            break
        except Exception as e:
            print(f"❌ 错误: {e}\n")

if __name__ == "__main__":
    main()

