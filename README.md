# OCR-题库-AI

一个基于 Windows 截图的答题辅助工具：先对截图中的题目做识别，优先查询本地题库，未命中时再调用兼容 OpenAI 接口的大模型答题。

## 当前功能

- 支持仅题库模式：启动时可以跳过大模型配置，直接使用题库。
- 支持离线 OCR 识题：题库匹配依赖本地 OCR，不依赖联网模型。
- 支持题库优先 + AI 兜底：题库未命中时，如果模型已配置，会继续调用 AI 答题。
- 支持自动读取和保存模型配置。
- 支持显示最接近的题库题目及对应答案，便于补题和调试 OCR 命中效果。
- 支持窗口预览与区域预览；如果预览图保存失败，只会跳过保存，不会导致程序退出。

## 运行环境

- Windows
- Python 3.7+

## 安装依赖

```bash
pip install -r requirements.txt
```

当前依赖见 `requirements.txt`：
- requests
- Pillow
- pywin32
- numpy
- rapidocr-onnxruntime

## 启动方式

```bash
python answer_helper.py
```

也可以先做一次语法检查：

```bash
python -m py_compile answer_helper.py
```

## 使用流程

1. 启动程序。
2. 选择是否启用大模型答题。
   - 选择否：会跳过大模型配置，并自动启用题库。
   - 选择是：可以读取已保存配置，或重新配置模型提供方、API Key、Base URL 和模型。
3. 如果启用了题库，可选择是否开启题库优先。
4. 可选设置固定窗口和截图区域。
5. 进入循环后，按回车截图识别：
   - 若题库可用，先使用离线 OCR 提取题目并查题库；
   - 若题库命中，直接输出题库答案；
   - 若题库未命中且模型可用，再调用 AI 答题；
   - 若题库未命中且模型不可用，则直接提示无法继续 AI 答题。

## 题库文件说明

程序会从以下位置加载题库：
- `question_banks/*.json`
- `answer_question_bank.json`

仓库默认只保留示例题库：
- `question_banks/example.json`

你可以按相同结构新增自己的题库文件。单条题目示例：

```json
{
  "entries": [
    {
      "question": "示例题目",
      "answer": "示例答案",
      "aliases": ["示例别名1", "示例别名2"]
    }
  ]
}
```

字段说明：
- `question`：题目正文
- `answer`：答案
- `aliases`：可选，题目的别名或 OCR 常见误识别版本

## 模型配置说明

程序运行时会读取并保存 `answer_helper_config.json`。

仓库额外提供了一个示例文件：`answer_helper_config.example.json`。你可以参考它的结构，或直接复制一份并命名为 `answer_helper_config.json` 后再填写自己的真实配置。

首次使用时也可以直接运行程序，按提示填写你的：
- 提供方名称
- API Key
- Base URL
- 模型 ID

注意：
- 不要把你自己的真实 `answer_helper_config.json` 或 API Key 上传到 GitHub。
- 目前支持 OpenAI 兼容接口；当提供方不支持 `/models` 时，也可以手动输入模型 ID。

## 已知限制

- 当前项目是 Windows 专用，依赖桌面截图和窗口枚举能力。
- 离线 OCR 的效果会影响题库命中率；必要时可通过补充 `aliases` 优化匹配效果。
- 题库未命中时能否继续答题，取决于当前模型是否已正确配置并可访问。
- 预览图保存不是核心流程；即使保存失败，也不影响截图识别和答题主流程。

## 仓库发布说明

为了适合直接上传到 GitHub，当前仓库已做以下处理：
- 移除了本地运行生成的预览图等产物；
- 不再提交运行时配置文件，改为提供 `answer_helper_config.example.json` 作为示例；
- `.gitignore` 已配置为默认忽略本地题库与本地配置，仅保留示例题库 `question_banks/example.json` 作为公开示例。

如果你要继续公开维护这个项目，建议在提交前再次检查：
- 是否误提交了真实 API Key
- 是否误提交了本地题库数据
- 是否误提交了运行生成的截图或缓存文件
- 如果你是通过 GitHub 网页手动上传文件，而不是通过 Git，请不要把本地真实题库和个人配置文件一起选中上传
