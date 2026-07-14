from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm import LLMClient, LLMUnavailable


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")
STOPWORDS = {"알려줘", "설명", "관련", "내용", "무엇", "어떻게", "인가요", "해줘", "좀"}


@dataclass(frozen=True)
class KnowledgeChunk:
    source: str
    section: str
    text: str


class KnowledgeService:
    def __init__(self, manual_dir: Path, llm: LLMClient) -> None:
        self.manual_dir = manual_dir
        self.llm = llm
        self.chunks = self._load_chunks()

    def _load_chunks(self) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for path in sorted(self.manual_dir.glob("*.md")):
            current_title = path.stem
            current_lines: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## ") and current_lines:
                    chunks.append(KnowledgeChunk(path.name, current_title, "\n".join(current_lines).strip()))
                    current_title = line[3:].strip()
                    current_lines = [line]
                else:
                    current_lines.append(line)
            if current_lines:
                chunks.append(KnowledgeChunk(path.name, current_title, "\n".join(current_lines).strip()))
        return [chunk for chunk in chunks if chunk.text]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [token.lower() for token in TOKEN_RE.findall(text) if len(token) >= 2 and token not in STOPWORDS]

    def search(self, question: str, limit: int = 3) -> list[tuple[float, KnowledgeChunk]]:
        query_tokens = self._tokens(question)
        if not query_tokens:
            return []
        document_frequency = {
            token: sum(1 for chunk in self.chunks if token in chunk.text.lower())
            for token in set(query_tokens)
        }
        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in self.chunks:
            body = chunk.text.lower()
            title = chunk.section.lower()
            score = 0.0
            for token in query_tokens:
                idf = math.log((len(self.chunks) + 1) / (document_frequency[token] + 1)) + 1
                score += body.count(token) * idf
                if token in title:
                    score += idf * 6
                if re.search(r"[0-9_]", token) and token in body:
                    score += idf * 2
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:limit]

    def query(self, question: str) -> dict[str, Any]:
        matches = self.search(question)
        if not matches:
            return {
                "kind": "knowledge",
                "question": question,
                "answer": "현재 등록된 업무 매뉴얼에서 질문과 직접 연결되는 내용을 찾지 못했습니다.",
                "citations": [],
                "generationEngine": "grounded_search",
            }

        citations = [
            {
                "source": chunk.source,
                "section": chunk.section,
                "excerpt": self._excerpt(chunk.text),
                "score": round(score, 3),
            }
            for score, chunk in matches
        ]
        evidence = "\n\n".join(
            f"[{index}] {chunk.source} / {chunk.section}\n{chunk.text[:1800]}"
            for index, (_, chunk) in enumerate(matches, start=1)
        )
        try:
            reply = self.llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "당신은 iM증권 사내 업무 매뉴얼 도우미입니다. 제공된 근거만 사용해 한국어로 "
                            "간결하고 실행 가능한 답변을 작성하세요. 근거 번호를 [1] 형식으로 표시하고, "
                            "근거에 없는 내용은 추측하지 마세요."
                        ),
                    },
                    {"role": "user", "content": f"질문: {question}\n\n근거:\n{evidence}"},
                ]
            )
            answer = reply.content
            engine = "llm"
            model = reply.model
        except LLMUnavailable:
            answer = self._grounded_summary(question, citations)
            engine = "grounded_search"
            model = None
        return {
            "kind": "knowledge",
            "question": question,
            "answer": answer,
            "citations": citations,
            "generationEngine": engine,
            "llmModel": model,
        }

    @staticmethod
    def _excerpt(text: str, limit: int = 360) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        compact = " ".join(lines)
        return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"

    @staticmethod
    def _grounded_summary(question: str, citations: list[dict[str, Any]]) -> str:
        lines = [f"질문과 가장 관련 있는 매뉴얼 근거를 찾았습니다: {question}"]
        for index, citation in enumerate(citations, start=1):
            lines.append(f"[{index}] {citation['excerpt']}")
        return "\n\n".join(lines)
