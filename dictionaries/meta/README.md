# Codex 外置翻译脚本（结构化提取 + 受控替换）

该目录提供一个无需重编译的翻译方案：

- 结构化提取：优先按 JSON 结构只翻译推理/状态字段（如 `reasoning delta`、`status_header`）。
- 受控替换：自动保护代码块、反引号内容、URL、路径、命令参数，避免误翻译代码与路径。

## 文件

- `translate_codex_stream.py`：主脚本。
- `combined_zh_en_dictionary.jsonl`：合并后的 EN→ZH 术语来源（已生成）。
- `zh_phrase_overrides.json`：高优先级短语覆盖（可手工维护）。

## 用法

### 1) 处理 JSONL 事件流（推荐）

```bash
cat input.jsonl | python3 dictionaries/meta/translate_codex_stream.py --mode jsonl > output.zh.jsonl
```

默认 `--delta-mode full-buffer`，会把分片 `delta` 累积后输出“当前完整翻译”，适合阅读推理流。

### 2) 自动识别（JSONL/文本混合）

```bash
cat input.log | python3 dictionaries/meta/translate_codex_stream.py --mode auto > output.zh.log
```

### 3) 查看统计

```bash
cat input.jsonl | python3 dictionaries/meta/translate_codex_stream.py --mode jsonl --stats > /tmp/out.jsonl
```

若需要保持“逐分片”行为，可改为：

```bash
cat input.jsonl | python3 dictionaries/meta/translate_codex_stream.py --mode jsonl --delta-mode chunk > output.zh.jsonl
```

## 与 Codex CLI 搭配

非交互模式可直接串流：

```bash
codex exec --json "your prompt" | python3 dictionaries/meta/translate_codex_stream.py --mode jsonl
```

## 说明

- 该方案是外置后处理，不需要重新编译二进制。
- 交互式全屏 TUI 的实时界面文本无法通过普通管道稳定拦截；建议使用 `--json` 流或会话日志做翻译显示。
