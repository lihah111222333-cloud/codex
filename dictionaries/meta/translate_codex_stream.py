#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DICTIONARY = ROOT / "meta" / "combined_zh_en_dictionary.jsonl"
DEFAULT_OVERRIDES = ROOT / "meta" / "zh_phrase_overrides.json"

PROTECT_PATTERNS = [
    re.compile(r"```[\s\S]*?```"),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"https?://[^\s)\]>]+"),
    re.compile(r"(?:^|(?<=\s))(?:~?/|\.{1,2}/|/|[A-Za-z]:\\)[^\s\"'`<>|]*"),
    re.compile(r"(?<!\w)--?[A-Za-z][\w-]*"),
    re.compile(r"\b[A-Z_][A-Z0-9_]{2,}\b"),
]
TOKEN_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z0-9]+)*")

TARGET_STATUS_KEYS = {"status_header", "status_details", "statusHeader", "statusDetails"}
TARGET_REASONING_KEYS = {
    "delta",
    "text",
    "summary",
    "summary_text",
    "summaryText",
    "message",
}
TARGET_STATUS_EVENT_TYPES = {
    "background_event",
    "collab_waiting_begin",
    "collab_waiting_end",
}
RESET_EVENT_TYPES = {
    "agent_reasoning",
    "agent_reasoning_raw_content",
    "agent_reasoning_section_break",
    "task_complete",
    "turn_complete",
}


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_key(text: str) -> str:
    return normalize_spaces(text).lower()


def has_english(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text))


def is_plain_english_term(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-_/.'&()+]{0,127}", text))


@dataclass
class TranslationStats:
    lines: int = 0
    translated_strings: int = 0
    json_records: int = 0


class DeltaAccumulator:
    def __init__(self) -> None:
        self.buffers: dict[str, str] = {}

    def rewrite_to_full_buffer(
        self,
        stream_key: str,
        delta: str,
        translator: "ControlledTranslator",
    ) -> str:
        full_source = self.buffers.get(stream_key, "") + delta
        self.buffers[stream_key] = full_source
        return translator.translate_text(full_source)

    def clear_by_record_id(self, record_id: str) -> None:
        prefix = f"{record_id}|"
        keys = [key for key in self.buffers if key.startswith(prefix)]
        for key in keys:
            del self.buffers[key]


class ControlledTranslator:
    def __init__(
        self,
        dictionary_map: dict[str, str],
        overrides: dict[str, str],
        min_phrase_words: int = 2,
        max_phrase_words: int = 8,
    ) -> None:
        self.dictionary_map = dictionary_map
        self.min_phrase_words = min_phrase_words
        self.max_phrase_words = max(min_phrase_words, max_phrase_words)
        override_items = sorted(
            (
                (normalize_key(source), target)
                for source, target in overrides.items()
                if source.strip() and target.strip()
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        self.override_patterns: list[tuple[re.Pattern[str], str]] = []
        for source, target in override_items:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])",
                re.IGNORECASE,
            )
            self.override_patterns.append((pattern, target))

    def translate_text(self, text: str) -> str:
        if not text.strip() or not has_english(text):
            return text

        protected_text, protected = self._protect(text)
        translated = self._apply_overrides(protected_text)
        translated = self._apply_dictionary(translated)
        return self._restore(translated, protected)

    def _protect(self, text: str) -> tuple[str, list[str]]:
        protected: list[str] = []

        def replacer(match: re.Match[str]) -> str:
            index = len(protected)
            protected.append(match.group(0))
            return f"⟪#{index}⟫"

        out = text
        for pattern in PROTECT_PATTERNS:
            out = pattern.sub(replacer, out)
        return out, protected

    def _restore(self, text: str, protected: list[str]) -> str:
        out = text
        for index in range(len(protected) - 1, -1, -1):
            out = out.replace(f"⟪#{index}⟫", protected[index])
        return out

    def _apply_overrides(self, text: str) -> str:
        out = text
        for pattern, replacement in self.override_patterns:
            out = pattern.sub(replacement, out)
        return out

    def _apply_dictionary(self, text: str) -> str:
        tokens = list(TOKEN_RE.finditer(text))
        if not tokens:
            return text

        out_parts: list[str] = []
        cursor = 0
        index = 0

        while index < len(tokens):
            best_match = None
            max_window = min(self.max_phrase_words, len(tokens) - index)

            for window in range(max_window, self.min_phrase_words - 1, -1):
                end_index = index + window - 1
                if not self._tokens_are_space_separated(text, tokens, index, end_index):
                    continue

                phrase = " ".join(tokens[idx].group(0) for idx in range(index, end_index + 1))
                replacement = self.dictionary_map.get(normalize_key(phrase))
                if replacement:
                    best_match = (tokens[index].start(), tokens[end_index].end(), replacement, end_index + 1)
                    break

            if best_match is None:
                index += 1
                continue

            start, end, replacement, next_index = best_match
            out_parts.append(text[cursor:start])
            out_parts.append(replacement)
            cursor = end
            index = next_index

        out_parts.append(text[cursor:])
        return "".join(out_parts)

    @staticmethod
    def _tokens_are_space_separated(
        text: str,
        tokens: list[re.Match[str]],
        start_index: int,
        end_index: int,
    ) -> bool:
        for idx in range(start_index, end_index):
            if not text[tokens[idx].end() : tokens[idx + 1].start()].isspace():
                return False
        return True


def load_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"overrides file must be a JSON object: {path}")
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def load_dictionary(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if (
                record.get("lang_src") != "en"
                or record.get("lang_tgt") not in {"zh", "zh-Hans", "zh-Hant"}
            ):
                continue

            source = normalize_spaces(str(record.get("term_src", "")))
            target = normalize_spaces(str(record.get("term_tgt", "")))
            if not source or not target:
                continue
            if not is_plain_english_term(source):
                continue

            key = normalize_key(source)
            if key in mapping:
                continue
            mapping[key] = target
    return mapping


def detect_event_type(node: Any) -> str | None:
    if isinstance(node, dict):
        value = node.get("type")
        if isinstance(value, str):
            return value

        payload = node.get("payload")
        if isinstance(payload, dict):
            msg = payload.get("msg")
            if isinstance(msg, dict):
                msg_type = msg.get("type")
                if isinstance(msg_type, str):
                    return msg_type

        msg = node.get("msg")
        if isinstance(msg, dict):
            msg_type = msg.get("type")
            if isinstance(msg_type, str):
                return msg_type

    return None


def extract_record_id(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None

    payload = node.get("payload")
    if isinstance(payload, dict):
        payload_id = payload.get("id")
        if isinstance(payload_id, str) and payload_id:
            return payload_id

    value = node.get("id")
    if isinstance(value, str) and value:
        return value

    item_id = node.get("item_id")
    if isinstance(item_id, str) and item_id:
        return item_id

    return None


def should_translate_field(key: str, event_type: str | None, parent_path: list[str]) -> bool:
    if key in TARGET_STATUS_KEYS:
        return True

    if event_type:
        lowered = event_type.lower()
        if "reasoning" in lowered and key in TARGET_REASONING_KEYS:
            return True
        if lowered in TARGET_STATUS_EVENT_TYPES and key in {"message", "text", "delta"}:
            return True

    if key in {"status_header", "status_details"}:
        return True

    parent_joined = ".".join(parent_path).lower()
    return "orchestration" in parent_joined and key in {"message", "text", "delta"}


def translate_json_node(
    node: Any,
    translator: ControlledTranslator,
    delta_accumulator: DeltaAccumulator,
    delta_mode: str,
    record_id: str | None,
    inherited_event_type: str | None = None,
    path: list[str] | None = None,
) -> Any:
    current_path = path or []
    node_event_type = detect_event_type(node) or inherited_event_type

    if isinstance(node, dict):
        output: dict[str, Any] = {}
        for key, value in node.items():
            if isinstance(value, str) and should_translate_field(key, node_event_type, current_path):
                should_expand_delta = (
                    delta_mode == "full-buffer"
                    and key == "delta"
                    and node_event_type is not None
                    and ("reasoning" in node_event_type.lower() or node_event_type.lower() in TARGET_STATUS_EVENT_TYPES)
                    and record_id is not None
                )
                if should_expand_delta:
                    stream_key = f"{record_id}|{node_event_type}|{'.'.join([*current_path, key])}"
                    output[key] = delta_accumulator.rewrite_to_full_buffer(
                        stream_key=stream_key,
                        delta=value,
                        translator=translator,
                    )
                else:
                    output[key] = translator.translate_text(value)
                continue

            output[key] = translate_json_node(
                value,
                translator,
                delta_accumulator=delta_accumulator,
                delta_mode=delta_mode,
                record_id=record_id,
                inherited_event_type=node_event_type,
                path=[*current_path, key],
            )
        return output

    if isinstance(node, list):
        return [
            translate_json_node(
                item,
                translator,
                delta_accumulator=delta_accumulator,
                delta_mode=delta_mode,
                record_id=record_id,
                inherited_event_type=node_event_type,
                path=current_path,
            )
            for item in node
        ]

    return node


def process_line(
    line: str,
    translator: ControlledTranslator,
    delta_accumulator: DeltaAccumulator,
    mode: str,
    delta_mode: str,
    stats: TranslationStats,
) -> str:
    stats.lines += 1
    content = line.rstrip("\n")
    newline = "\n" if line.endswith("\n") else ""

    should_try_json = mode in {"auto", "jsonl"} and content.strip().startswith(("{", "["))
    if should_try_json:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            if mode == "jsonl":
                return line
        else:
            event_type = detect_event_type(parsed)
            record_id = extract_record_id(parsed)
            if (
                delta_mode == "full-buffer"
                and record_id is not None
                and event_type in RESET_EVENT_TYPES
            ):
                delta_accumulator.clear_by_record_id(record_id)

            translated = translate_json_node(
                parsed,
                translator,
                delta_accumulator=delta_accumulator,
                delta_mode=delta_mode,
                record_id=record_id,
            )
            encoded = json.dumps(translated, ensure_ascii=False, separators=(",", ":"))
            if encoded != content:
                stats.translated_strings += 1
            stats.json_records += 1
            return encoded + newline

    if mode == "jsonl":
        return line

    translated_text = translator.translate_text(content)
    if translated_text != content:
        stats.translated_strings += 1
    return translated_text + newline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate Codex reasoning/status text via structured extraction + controlled replacement.",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "jsonl", "text"],
        default="auto",
        help="Input mode: auto-detect JSONL per line, force JSONL, or plain text lines.",
    )
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=DEFAULT_DICTIONARY,
        help=f"Dictionary JSONL path (default: {DEFAULT_DICTIONARY})",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=DEFAULT_OVERRIDES,
        help=f"Overrides JSON path (default: {DEFAULT_OVERRIDES})",
    )
    parser.add_argument(
        "--min-phrase-words",
        type=int,
        default=2,
        help="Minimum English phrase length for dictionary matching.",
    )
    parser.add_argument(
        "--max-phrase-words",
        type=int,
        default=8,
        help="Maximum English phrase length for dictionary matching.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print processing stats to stderr.",
    )
    parser.add_argument(
        "--delta-mode",
        choices=["full-buffer", "chunk"],
        default="full-buffer",
        help="For JSON delta fields: output translated full buffer, or translate each chunk in place.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.dictionary.exists():
        raise SystemExit(f"dictionary file not found: {args.dictionary}")

    dictionary_map = load_dictionary(args.dictionary)
    overrides = load_overrides(args.overrides)
    translator = ControlledTranslator(
        dictionary_map=dictionary_map,
        overrides=overrides,
        min_phrase_words=max(1, args.min_phrase_words),
        max_phrase_words=args.max_phrase_words,
    )

    stats = TranslationStats()
    delta_accumulator = DeltaAccumulator()
    for line in sys.stdin:
        sys.stdout.write(
            process_line(
                line,
                translator,
                delta_accumulator=delta_accumulator,
                mode=args.mode,
                delta_mode=args.delta_mode,
                stats=stats,
            )
        )
    sys.stdout.flush()

    if args.stats:
        print(
            json.dumps(
                {
                    "lines": stats.lines,
                    "json_records": stats.json_records,
                    "translated_lines": stats.translated_strings,
                    "dictionary_terms_loaded": len(dictionary_map),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
