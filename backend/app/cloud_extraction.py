"""OpenAI-compatible DeepSeek client for unresolved target fields.

The client only ever receives selected, redacted slices; it never sees whole
materials, the alias map, or internal source references. It logs summaries
only — never source, prompt, or response bodies.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("icrm.cloud")

PROMPT_VERSION = "icrm-extract-prompt-1"
EXTRACTOR_VERSION = "icrm-deepseek-client-1"

SYSTEM_PROMPT = (
    "你是信贷材料字段抽取助手。只从给定文本中抽取指定字段的值，不推断、不改写、不编造原文中没有的信息。"
    "返回 JSON 对象 {\"results\": [{\"field_key\": \"...\", \"value\": \"原文中的值\", "
    "\"confidence\": 0.0-1.0}]}。无法从文本确定某字段时，不要包含该字段。"
    "values 必须逐字来自文本，金额保留原数字和单位，利率保留百分比。"
)


class CloudExtractionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RedactedSlice:
    field_key: str
    label: str
    text: str
    source_refs: list[dict]


@dataclass(frozen=True)
class CloudCandidate:
    field_key: str
    value: str
    confidence: float
    source_refs: list[dict]


class DeepSeekClient:
    """Thin OpenAI-compatible chat-completions client, mockable in tests."""

    extractor_version = EXTRACTOR_VERSION

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def extract(self, slices: list[RedactedSlice]) -> list[CloudCandidate]:
        if not self.enabled:
            raise CloudExtractionError("deepseek_disabled")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        [
                            {"field_key": item.field_key, "label": item.label, "text": item.text}
                            for item in slices
                        ],
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        request = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        logger.info(
            "deepseek extraction call field_keys=%s model=%s",
            [item.field_key for item in slices],
            self.model,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning("deepseek call failed code=deepseek_unavailable")
            raise CloudExtractionError("deepseek_unavailable") from exc
        try:
            content = body["choices"][0]["message"]["content"]
            data = json.loads(content)
            raw_results = data.get("results", []) if isinstance(data, dict) else []
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("deepseek response unparsable code=deepseek_bad_response")
            raise CloudExtractionError("deepseek_bad_response") from exc
        requested = {item.field_key for item in slices}
        results: list[CloudCandidate] = []
        for raw in raw_results:
            if not isinstance(raw, dict) or raw.get("field_key") not in requested:
                continue
            value = str(raw.get("value", "")).strip()
            if not value:
                continue
            try:
                confidence = float(raw.get("confidence", 0.8))
            except (TypeError, ValueError):
                confidence = 0.8
            confidence = max(0.0, min(1.0, confidence))
            results.append(
                CloudCandidate(
                    field_key=raw["field_key"],
                    value=value,
                    confidence=confidence,
                    source_refs=[
                        ref
                        for item in slices
                        if item.field_key == raw["field_key"]
                        for ref in item.source_refs
                    ],
                )
            )
        return results
