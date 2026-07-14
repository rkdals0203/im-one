from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from im_one_agent.domain import BUSINESS_RULES, METRICS, ROLE_TABLE_POLICY, TABLES, MetricSpec, TableSpec, normalize_user_role
from im_one_agent.env import load_project_env

load_project_env()

TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_가-힣]+")
EMBEDDING_DIMENSIONS = 96
FOLLOW_UP_KEYWORDS = (
    "그중",
    "그 중",
    "거기서",
    "결과에서",
    "방금",
    "상위",
    "이전",
    "앞에서",
    "해당",
)
LOCAL_NO_AUTH_VALUES = {"1", "true", "yes", "on"}
LOCAL_EMBEDDING_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class RetrievalScore:
    metric_name: str
    keyword_hits: int
    token_overlap: int
    vector_similarity: float
    total_score: float
    embedding_source: str = "local"


@dataclass(frozen=True)
class SchemaContext:
    matched_metrics: tuple[MetricSpec, ...]
    tables: tuple[TableSpec, ...]
    business_rules: tuple[str, ...]
    example_queries: tuple[str, ...]
    retrieval_scores: tuple[RetrievalScore, ...] = ()
    retrieval_confidence: str = "unknown"
    clarification_options: tuple[str, ...] = ()

    @property
    def allowed_table_names(self) -> set[str]:
        return {table.name for table in self.tables}


def retrieve_schema(question: str, user_role: str = "branch_manager") -> SchemaContext:
    """Return the smallest useful schema context for a business question."""
    role_allowed = ROLE_TABLE_POLICY[normalize_user_role(user_role)]
    allowed_metrics = tuple(metric for metric in METRICS if metric_is_allowed(metric, role_allowed))

    scored: list[tuple[float, MetricSpec, RetrievalScore]] = []
    for metric in allowed_metrics:
        retrieval_score = score_metric(question, metric)
        if retrieval_score.total_score > 0:
            scored.append((retrieval_score.total_score, metric, retrieval_score))

    if not scored and allowed_metrics:
        fallback_score = RetrievalScore(allowed_metrics[0].name, 0, 0, 0.0, 1.0)
        scored = [(1.0, allowed_metrics[0], fallback_score)]

    scored.sort(key=lambda item: item[0], reverse=True)
    retrieval_scores = tuple(score for _, _, score in scored[:4])
    retrieval_confidence = classify_retrieval_confidence(retrieval_scores)
    metric_limit = 1 if retrieval_confidence == "high" else 2
    matched_metrics = tuple(metric for _, metric, _ in scored[:metric_limit])
    clarification_options = build_clarification_options(matched_metrics, retrieval_confidence)

    table_names: set[str] = set()
    for metric in matched_metrics:
        table_names.update(metric.tables)

    table_names &= role_allowed
    tables = tuple(TABLES[name] for name in sorted(table_names))
    examples = tuple(metric.sample_question for metric in matched_metrics)

    return SchemaContext(
        matched_metrics=matched_metrics,
        tables=tables,
        business_rules=BUSINESS_RULES,
        example_queries=examples,
        retrieval_scores=retrieval_scores,
        retrieval_confidence=retrieval_confidence,
        clarification_options=clarification_options,
    )


def extend_schema_with_follow_up_context(
    question: str,
    context: SchemaContext,
    conversation_context: dict[str, object],
    user_role: str = "branch_manager",
) -> SchemaContext:
    if not conversation_context or not is_follow_up_question(question):
        return context
    if conversation_context.get("previous_validation_allowed") is False:
        return context

    role_allowed = ROLE_TABLE_POLICY[normalize_user_role(user_role)]
    table_names = set[str]()
    for table_name in string_items(conversation_context.get("previous_tables")):
        if table_name in TABLES and table_name in role_allowed:
            table_names.add(table_name)

    metric_by_name: dict[str, MetricSpec] = {}
    metrics_by_catalog_name = {metric.name: metric for metric in METRICS}
    for metric_name in string_items(conversation_context.get("previous_metrics")):
        metric = metrics_by_catalog_name.get(metric_name)
        if metric is not None and metric_is_allowed(metric, role_allowed):
            metric_by_name.setdefault(metric.name, metric)
            table_names.update(table_name for table_name in metric.tables if table_name in role_allowed)

    has_explicit_current_signal = any(score.keyword_hits > 0 for score in context.retrieval_scores)
    include_current_context = context.retrieval_confidence != "low" or has_explicit_current_signal or not metric_by_name
    if include_current_context:
        for metric in context.matched_metrics:
            metric_by_name.setdefault(metric.name, metric)
            table_names.update(table_name for table_name in metric.tables if table_name in role_allowed)
        table_names.update(table.name for table in context.tables)

    business_rules = context.business_rules
    follow_up_rule = "후속 질문은 이전 실행의 테이블과 지표 context를 함께 사용합니다."
    if follow_up_rule not in business_rules:
        business_rules = business_rules + (follow_up_rule,)

    examples = tuple(dict.fromkeys(context.example_queries + tuple(metric.sample_question for metric in metric_by_name.values())))

    return SchemaContext(
        matched_metrics=tuple(metric_by_name.values()),
        tables=tuple(TABLES[table_name] for table_name in sorted(table_names)),
        business_rules=business_rules,
        example_queries=examples,
        retrieval_scores=context.retrieval_scores,
        retrieval_confidence=context.retrieval_confidence,
        clarification_options=context.clarification_options,
    )


def metric_is_allowed(metric: MetricSpec, role_allowed: set[str]) -> bool:
    return set(metric.tables).issubset(role_allowed)


def classify_retrieval_confidence(scores: tuple[RetrievalScore, ...]) -> str:
    if not scores:
        return "low"

    top_score = scores[0].total_score
    runner_up = scores[1].total_score if len(scores) > 1 else 0.0
    close_runner_up = runner_up > 0 and runner_up / top_score >= 0.65

    if top_score >= 8 and not close_runner_up:
        return "high"
    if top_score >= 4 and not close_runner_up:
        return "medium"
    return "low"


def build_clarification_options(metrics: tuple[MetricSpec, ...], confidence: str) -> tuple[str, ...]:
    if confidence != "low":
        return ()
    return tuple(
        f"{metric.name}: {metric.description} 기준으로 볼까요?"
        for metric in metrics[:3]
    )


def is_follow_up_question(question: str) -> bool:
    normalized = question.lower()
    return any(keyword.lower() in normalized for keyword in FOLLOW_UP_KEYWORDS)


def string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def score_metric(question: str, metric: MetricSpec) -> RetrievalScore:
    normalized = question.lower()
    keyword_hits = sum(1 for keyword in metric.keywords if keyword.lower() in normalized)
    question_tokens = tokenize(question)
    document = metric_document(metric)
    document_tokens = tokenize(document)
    token_overlap = len(question_tokens & document_tokens)
    question_embedding, question_embedding_source = build_embedding(question)
    document_embedding, document_embedding_source = build_embedding(document)
    vector_similarity = cosine_similarity(question_embedding, document_embedding)
    embedding_source = (
        "remote"
        if question_embedding_source == "remote" and document_embedding_source == "remote"
        else "local"
    )
    total_score = keyword_hits * 4.0 + token_overlap * 1.2 + vector_similarity * 3.0

    return RetrievalScore(
        metric_name=metric.name,
        keyword_hits=keyword_hits,
        token_overlap=token_overlap,
        vector_similarity=round(vector_similarity, 6),
        total_score=round(total_score, 6),
        embedding_source=embedding_source,
    )


def metric_document(metric: MetricSpec) -> str:
    table_text = " ".join(
        " ".join((table.name, table.description, " ".join(table.columns)))
        for table in (TABLES[table_name] for table_name in metric.tables)
    )
    return " ".join(
        (
            metric.name,
            metric.description,
            metric.definition,
            " ".join(metric.keywords),
            " ".join(metric.related_columns),
            metric.date_column,
            metric.default_period,
            " ".join(metric.filters),
            " ".join(metric.join_paths),
            metric.default_grouping,
            metric.sample_question,
            table_text,
        )
    )


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text)}


def local_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> tuple[float, ...]:
    vector = [0.0] * dimensions
    tokens = tokenize(text)
    for token in tokens:
        features = {token}
        if len(token) >= 3:
            features.update(token[index : index + 3] for index in range(len(token) - 2))
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=4).digest()
            bucket = int.from_bytes(digest[:2], "big") % dimensions
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return tuple(vector)
    return normalize_vector(vector)


class EmbeddingError(RuntimeError):
    """Raised when the configured embedding endpoint cannot return a vector."""


def build_embedding(text: str) -> tuple[tuple[float, ...], str]:
    if not remote_embeddings_configured():
        return local_embedding(text), "local"

    try:
        return remote_embedding(text), "remote"
    except EmbeddingError:
        return local_embedding(text), "local"


def remote_embeddings_configured() -> bool:
    return bool(configured_embedding_model() and (os.getenv("OPENAI_API_KEY") or local_embedding_no_auth_enabled()))


def remote_embedding(text: str) -> tuple[float, ...]:
    api_key = os.getenv("OPENAI_API_KEY")
    model = configured_embedding_model()
    endpoint = configured_embedding_base_url()
    if not model:
        raise EmbeddingError("Embedding endpoint model is not configured.")
    if not api_key and not local_embedding_no_auth_enabled(endpoint):
        raise EmbeddingError("Embedding endpoint credentials are not configured.")

    endpoint = endpoint.rstrip("/")
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        f"{endpoint}/embeddings",
        data=json.dumps({"model": model, "input": text}).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=float(os.getenv("IM_ONE_EMBEDDING_TIMEOUT", "20"))) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
        vector = raw_response["data"][0]["embedding"]
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise EmbeddingError(f"Embedding generation failed: {exc}") from exc

    if not isinstance(vector, list) or not vector:
        raise EmbeddingError("Embedding endpoint returned an empty vector.")

    try:
        return normalize_vector(float(value) for value in vector)
    except (TypeError, ValueError) as exc:
        raise EmbeddingError("Embedding endpoint returned a non-numeric vector.") from exc


def configured_embedding_model() -> str:
    return os.getenv("IM_ONE_EMBEDDING_MODEL", "")


def configured_embedding_base_url() -> str:
    return (
        os.getenv("IM_ONE_EMBEDDING_BASE_URL")
        or os.getenv("IM_ONE_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )


def local_embedding_no_auth_enabled(endpoint: str | None = None) -> bool:
    raw_flag = os.getenv("IM_ONE_EMBEDDING_ALLOW_LOCAL_NO_AUTH", "").strip().lower()
    if raw_flag not in LOCAL_NO_AUTH_VALUES:
        return False

    parsed = urlparse(endpoint or configured_embedding_base_url())
    hostname = parsed.hostname or ""
    return hostname in LOCAL_EMBEDDING_HOSTS


def normalize_vector(values) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return tuple(value / norm for value in vector)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right:
        return 0.0
    return sum(left_value * right_value for left_value, right_value in zip(left, right))
