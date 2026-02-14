#!/usr/bin/env python3

from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


ROOT = Path(__file__).resolve().parents[1]
OUT_JSONL = ROOT / "meta" / "combined_zh_en_dictionary.jsonl"
OUT_SUMMARY = ROOT / "meta" / "combined_zh_en_dictionary_summary.json"

CEDICT_PATH = ROOT / "cc-cedict" / "cedict_1_0_ts_utf-8_mdbg.txt.gz"
MS_ZIP_PATH = ROOT / "microsoft-terminology" / "MicrosoftTermCollection.zip"
CLDR_ZIP_PATH = ROOT / "cldr" / "cldr-core-latest.zip"

MS_HANS_TBX = "CHINESE (SIMPLIFIED).tbx"
MS_HANT_TBX = "CHINESE (TRADITIONAL).tbx"
CLDR_EN_XML = "common/main/en.xml"
CLDR_ZH_XML = "common/main/zh.xml"

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class EmitState:
    out_f: TextIO
    seen: set[tuple]
    counts: defaultdict[str, int]

    def emit(self, unique_key: tuple, record: dict) -> None:
        if unique_key in self.seen:
            return
        self.seen.add(unique_key)
        self.counts[record["source"]] += 1
        self.out_f.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_cedict(state: EmitState) -> None:
    pattern = re.compile(r"^(\S+)\s+(\S+)\s+\[(.+?)\]\s+/(.+)/$")
    with gzip.open(CEDICT_PATH, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = pattern.match(line)
            if not match:
                continue

            zh_trad, zh_simp, pinyin, defs_str = match.groups()
            defs = [item.strip() for item in defs_str.strip("/").split("/") if item.strip()]
            for en in defs:
                record = {
                    "source": "cc-cedict",
                    "lang_src": "zh-Hans",
                    "term_src": zh_simp,
                    "lang_tgt": "en",
                    "term_tgt": en,
                    "meta": {
                        "zh_traditional": zh_trad,
                        "pinyin": pinyin,
                    },
                }
                state.emit(("cc-cedict", zh_simp, en, pinyin), record)


def _unique_terms(lang_set: ET.Element) -> list[str]:
    terms = []
    for term in lang_set.findall(".//term"):
        if term.text:
            text = term.text.strip()
            if text:
                terms.append(text)
    # Preserve order while deduplicating
    return list(dict.fromkeys(terms))


def parse_microsoft_tbx(state: EmitState, member_name: str, zh_lang: str) -> None:
    with zipfile.ZipFile(MS_ZIP_PATH) as zf:
        with zf.open(member_name) as xml_file:
            for _, elem in ET.iterparse(xml_file, events=("end",)):
                if elem.tag != "termEntry":
                    continue

                en_terms: list[str] = []
                zh_terms: list[str] = []

                for lang_set in elem.findall("langSet"):
                    lang = lang_set.attrib.get(XML_LANG) or lang_set.attrib.get("xml:lang")
                    terms = _unique_terms(lang_set)
                    if lang == "en-US":
                        en_terms.extend(terms)
                    elif lang == zh_lang:
                        zh_terms.extend(terms)

                en_terms = list(dict.fromkeys(en_terms))
                zh_terms = list(dict.fromkeys(zh_terms))

                for en in en_terms:
                    for zh in zh_terms:
                        record = {
                            "source": "microsoft-terminology",
                            "lang_src": "en",
                            "term_src": en,
                            "lang_tgt": zh_lang,
                            "term_tgt": zh,
                            "meta": {
                                "tbx_file": member_name,
                            },
                        }
                        state.emit(("microsoft-terminology", zh_lang, en, zh), record)

                elem.clear()


def _extract_cldr_locale_display_map(xml_bytes: bytes) -> dict[str, str]:
    root = ET.fromstring(xml_bytes)
    locale_display = root.find("localeDisplayNames")
    if locale_display is None:
        return {}

    out: dict[str, str] = {}

    def walk(node: ET.Element, path_parts: list[str]) -> None:
        for child in list(node):
            tag = child.tag
            attrs = []
            child_type = child.get("type")
            if child_type:
                attrs.append(f"type={child_type}")
            if child.get("key"):
                attrs.append(f"key={child.get('key')}")
            if child.get("alt"):
                attrs.append(f"alt={child.get('alt')}")
            if child.get("draft"):
                attrs.append(f"draft={child.get('draft')}")
            token = tag if not attrs else f"{tag}[{','.join(attrs)}]"
            next_path = path_parts + [token]

            if list(child):
                walk(child, next_path)
                continue

            if child.get("alt") or child.get("draft"):
                continue
            if child.text is None:
                continue

            text = child.text.strip()
            if not text:
                continue
            if child_type is None and child.get("key") is None:
                continue

            out["/".join(next_path)] = text

    walk(locale_display, ["localeDisplayNames"])
    return out


def parse_cldr_locale_display(state: EmitState) -> None:
    with zipfile.ZipFile(CLDR_ZIP_PATH) as zf:
        en_map = _extract_cldr_locale_display_map(zf.read(CLDR_EN_XML))
        zh_map = _extract_cldr_locale_display_map(zf.read(CLDR_ZH_XML))

    common_keys = sorted(set(en_map).intersection(zh_map))
    for key in common_keys:
        en = en_map[key]
        zh = zh_map[key]
        if not en or not zh or en == zh:
            continue
        record = {
            "source": "cldr-locale-display",
            "lang_src": "en",
            "term_src": en,
            "lang_tgt": "zh-Hans",
            "term_tgt": zh,
            "meta": {
                "cldr_key": key,
            },
        }
        state.emit(("cldr-locale-display", key, en, zh), record)


def main() -> None:
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    counts: defaultdict[str, int] = defaultdict(int)
    seen: set[tuple] = set()

    with OUT_JSONL.open("w", encoding="utf-8") as out_f:
        state = EmitState(out_f=out_f, seen=seen, counts=counts)
        parse_cedict(state)
        parse_microsoft_tbx(state, MS_HANS_TBX, "zh-Hans")
        parse_microsoft_tbx(state, MS_HANT_TBX, "zh-Hant")
        parse_cldr_locale_display(state)

    summary = {
        "output_file": str(OUT_JSONL),
        "total_records": int(sum(counts.values())),
        "records_by_source": dict(sorted(counts.items())),
        "sources_attempted": [
            "cc-cedict",
            "microsoft-terminology (zh-Hans/zh-Hant)",
            "cldr-locale-display (en -> zh-Hans)",
            "iate (downloaded source page only; no machine-export merged)",
            "unterm (downloaded source page only; no machine-export merged)",
        ],
    }
    OUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
