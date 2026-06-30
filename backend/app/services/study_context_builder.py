"""Study-specific context builder built on the HelloAgents GSSC pattern."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hello_agents.context.builder import count_tokens


SECTION_ORDER = [
    "Role & Policies",
    "Learning Goal",
    "Current Task",
    "Current Topic",
    "Learner State",
    "Evidence",
    "Mistake History",
    "Learning Notes",
    "Conversation Context",
    "Output Spec",
]


@dataclass
class LearningContextPacket:
    """A scored packet of learning-related context."""

    section: str
    content: str
    priority: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    token_count: int = 0
    relevance_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.token_count:
            self.token_count = count_tokens(self.content)


@dataclass
class StudyContextConfig:
    """Configuration for StudyContextBuilder."""

    max_tokens: int = 4000
    reserve_ratio: float = 0.15
    min_relevance: float = 0.1
    max_items_per_section: int = 5
    enable_compression: bool = True

    def available_tokens(self) -> int:
        return int(self.max_tokens * (1 - self.reserve_ratio))


class StudyContextBuilder:
    """Builds structured study prompts using Gather/Select/Structure/Compress."""

    def __init__(self, config: StudyContextConfig | None = None):
        self.config = config or StudyContextConfig()

    def build(
        self,
        *,
        learning_goal: str,
        current_task: str,
        current_topic: str,
        learner_state: Any = None,
        evidence: list[Any] | None = None,
        mistake_history: list[Any] | None = None,
        learning_notes: list[Any] | None = None,
        conversation_context: list[Any] | None = None,
        output_spec: str | None = None,
        role_policies: str | None = None,
        additional_packets: list[LearningContextPacket] | None = None,
    ) -> str:
        packets = self.gather(
            learning_goal=learning_goal,
            current_task=current_task,
            current_topic=current_topic,
            learner_state=learner_state,
            evidence=evidence or [],
            mistake_history=mistake_history or [],
            learning_notes=learning_notes or [],
            conversation_context=conversation_context or [],
            output_spec=output_spec,
            role_policies=role_policies,
            additional_packets=additional_packets or [],
        )
        query = " ".join([learning_goal, current_task, current_topic]).strip()
        selected = self.select(packets, query=query)
        structured = self.structure(
            selected_packets=selected,
            learning_goal=learning_goal,
            current_task=current_task,
            current_topic=current_topic,
            learner_state=learner_state,
            output_spec=output_spec,
            role_policies=role_policies,
        )
        return self.compress(structured)

    def gather(
        self,
        *,
        learning_goal: str,
        current_task: str,
        current_topic: str,
        learner_state: Any = None,
        evidence: list[Any] | None = None,
        mistake_history: list[Any] | None = None,
        learning_notes: list[Any] | None = None,
        conversation_context: list[Any] | None = None,
        output_spec: str | None = None,
        role_policies: str | None = None,
        additional_packets: list[LearningContextPacket] | None = None,
    ) -> list[LearningContextPacket]:
        packets = [
            LearningContextPacket("Learning Goal", self._stringify(learning_goal), priority=1.0),
            LearningContextPacket("Current Task", self._stringify(current_task), priority=1.0),
            LearningContextPacket("Current Topic", self._stringify(current_topic), priority=1.0),
        ]

        if role_policies:
            packets.append(
                LearningContextPacket("Role & Policies", self._stringify(role_policies), priority=1.0)
            )
        if learner_state:
            packets.append(
                LearningContextPacket("Learner State", self._stringify(learner_state), priority=0.9)
            )
        if output_spec:
            packets.append(
                LearningContextPacket("Output Spec", self._stringify(output_spec), priority=0.9)
            )

        for item in evidence or []:
            packets.append(LearningContextPacket("Evidence", self._stringify(item), priority=0.85))
        for item in mistake_history or []:
            packets.append(
                LearningContextPacket("Mistake History", self._stringify(item), priority=0.8)
            )
        for item in learning_notes or []:
            packets.append(
                LearningContextPacket("Learning Notes", self._stringify(item), priority=0.75)
            )
        for item in conversation_context or []:
            packets.append(
                LearningContextPacket("Conversation Context", self._stringify(item), priority=0.7)
            )

        packets.extend(additional_packets or [])
        return packets

    def select(self, packets: list[LearningContextPacket], query: str) -> list[LearningContextPacket]:
        query_tokens = self._tokenize(query)
        scored: list[tuple[float, LearningContextPacket]] = []

        for packet in packets:
            content_tokens = self._tokenize(packet.content)
            overlap = len(query_tokens & content_tokens)
            packet.relevance_score = overlap / max(len(query_tokens), 1)
            recency = math.exp(-max((datetime.utcnow() - packet.timestamp).total_seconds(), 0) / 3600)
            score = packet.priority * 0.65 + packet.relevance_score * 0.25 + recency * 0.10
            scored.append((score, packet))

        available_tokens = self.config.available_tokens()
        used_tokens = 0
        selected: list[LearningContextPacket] = []
        selected_counts: dict[str, int] = {}

        for _score, packet in sorted(scored, key=lambda item: item[0], reverse=True):
            if packet.relevance_score < self.config.min_relevance and packet.priority < 0.95:
                continue
            if selected_counts.get(packet.section, 0) >= self.config.max_items_per_section:
                continue
            if used_tokens + packet.token_count > available_tokens:
                continue
            selected.append(packet)
            selected_counts[packet.section] = selected_counts.get(packet.section, 0) + 1
            used_tokens += packet.token_count

        return selected

    def structure(
        self,
        *,
        selected_packets: list[LearningContextPacket],
        learning_goal: str,
        current_task: str,
        current_topic: str,
        learner_state: Any = None,
        output_spec: str | None = None,
        role_policies: str | None = None,
    ) -> str:
        grouped: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
        for packet in selected_packets:
            grouped.setdefault(packet.section, []).append(packet.content.strip())

        grouped["Learning Goal"] = grouped["Learning Goal"] or [self._stringify(learning_goal)]
        grouped["Current Task"] = grouped["Current Task"] or [self._stringify(current_task)]
        grouped["Current Topic"] = grouped["Current Topic"] or [self._stringify(current_topic)]
        grouped["Learner State"] = grouped["Learner State"] or [
            self._stringify(learner_state) if learner_state else "暂无学习者状态记录。"
        ]
        grouped["Role & Policies"] = grouped["Role & Policies"] or [
            role_policies or "请保持准确、友好、循序渐进，并优先依据检索证据回答。"
        ]
        grouped["Output Spec"] = grouped["Output Spec"] or [
            output_spec or "请清晰作答，引用最相关的证据，并给出下一步学习建议。"
        ]
        grouped["Evidence"] = grouped["Evidence"] or ["暂未检索到外部证据。"]
        grouped["Mistake History"] = grouped["Mistake History"] or ["暂无历史错题记录。"]
        grouped["Learning Notes"] = grouped["Learning Notes"] or ["暂无已保存的学习笔记。"]
        grouped["Conversation Context"] = grouped["Conversation Context"] or [
            "暂无历史对话上下文。"
        ]

        sections: list[str] = []
        for section in SECTION_ORDER:
            body = "\n".join(self._to_bullets(grouped[section]))
            sections.append(f"[{section}]\n{body}")
        return "\n\n".join(sections)

    def compress(self, context: str) -> str:
        if not self.config.enable_compression:
            return context

        available_tokens = self.config.available_tokens()
        if count_tokens(context) <= available_tokens:
            return context

        lines = context.splitlines()
        header_indexes = [
            index for index, line in enumerate(lines) if line.startswith("[") and line.endswith("]")
        ]
        header_token_budget = sum(count_tokens(lines[index]) for index in header_indexes)

        compressed_lines: list[str] = []
        used_tokens = 0
        for index, line in enumerate(lines):
            is_header = index in header_indexes
            if is_header:
                compressed_lines.append(line)
                used_tokens += count_tokens(line)
                continue

            remaining = available_tokens - max(used_tokens, header_token_budget)
            if remaining <= 0:
                if line.strip():
                    compressed_lines.append("- Truncated to fit token budget.")
                continue

            line_tokens = count_tokens(line)
            if line_tokens <= remaining:
                compressed_lines.append(line)
                used_tokens += line_tokens
            elif line.strip():
                shortened = line[: max(32, remaining * 4)].rstrip()
                compressed_lines.append(f"{shortened} ...")
                used_tokens = available_tokens

        return "\n".join(compressed_lines)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return set(re.findall(r"\w+", text.lower()))

    @staticmethod
    def _to_bullets(items: list[str]) -> list[str]:
        return [item if item.startswith("- ") else f"- {item}" for item in items if item]

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            lines = []
            for item in value:
                item_text = StudyContextBuilder._stringify(item)
                if item_text:
                    lines.append(item_text)
            return "\n".join(lines)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        return str(value).strip()
