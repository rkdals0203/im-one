from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AssistantRequest(APIModel):
    session_id: str | None = Field(default=None, alias="sessionId", max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    role: str = Field(default="branch_manager", max_length=40)
    branch_id: int = Field(default=1, alias="branchId", ge=1, le=10)
    workspace_hint: Literal["knowledge", "data", "expense"] | None = Field(default=None, alias="workspaceHint")


class KnowledgeRequest(APIModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, alias="sessionId", max_length=80)
    role: str = "branch_manager"
    branch_id: int = Field(default=1, alias="branchId", ge=1, le=10)


class DataQueryRequest(APIModel):
    question: str = Field(min_length=1, max_length=2000)
    role: str = "branch_manager"
    branch_id: int = Field(default=1, alias="branchId", ge=1, le=10)
    session_id: str | None = Field(default=None, alias="sessionId", max_length=80)
    conversation_context: dict[str, Any] = Field(default_factory=dict, alias="conversationContext")


class ExportRequest(APIModel):
    session_id: str = Field(alias="sessionId", min_length=1, max_length=80)
    export_type: Literal["csv", "report"] = Field(default="csv", alias="exportType")
    role: str = "branch_manager"
    branch_id: int = Field(default=1, alias="branchId", ge=1, le=10)


class ExpenseActionRequest(APIModel):
    session_id: str = Field(alias="sessionId", min_length=1, max_length=80)
    action: Literal["confirm", "reject"]
    confirmation_token: str | None = Field(default=None, alias="confirmationToken", max_length=80)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=120)
    role: str = "branch_manager"
    branch_id: int = Field(default=1, alias="branchId", ge=1, le=10)


class Citation(APIModel):
    source: str
    section: str
    excerpt: str
    score: float


class KnowledgePayload(APIModel):
    kind: Literal["knowledge"] = "knowledge"
    question: str
    answer: str
    citations: list[Citation]
    generation_engine: str = Field(alias="generationEngine")
    llm_model: str | None = Field(default=None, alias="llmModel")


class DataPayload(APIModel):
    kind: Literal["data"] = "data"
    question: str
    answer: str
    explanation: str
    columns: list[str]
    column_metadata: list[dict[str, Any]] = Field(default_factory=list, alias="columnMetadata")
    rows: list[dict[str, Any]]
    row_count: int = Field(alias="rowCount")
    sql: str
    generated_sql: str | None = Field(default=None, alias="generatedSql")
    execution_ms: float | None = Field(default=None, alias="executionMs")
    validation: dict[str, Any] = Field(default_factory=dict)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    execution_trace: list[dict[str, Any]] = Field(default_factory=list, alias="executionTrace")
    conversation_context: dict[str, Any] = Field(default_factory=dict, alias="conversationContext")


class ExpenseItem(APIModel):
    id: int
    dept: str
    date: str
    title: str
    amount: int
    account: str
    status: str
    source_usage_keys: list[str] = Field(default_factory=list, alias="sourceUsageKeys")


class BudgetItem(APIModel):
    value: str
    code: str
    name: str
    allocated: int
    used: int
    remaining: int


class PendingExpenseAction(APIModel):
    token: str
    type: Literal["create", "approve", "cancel"]
    item_ids: list[int] | None = Field(default=None, alias="itemIds")
    items: list[dict[str, Any]] | None = None
    draft: dict[str, Any] | None = None


class ExpenseOverview(APIModel):
    items: list[ExpenseItem]
    budgets: list[BudgetItem]
    pending_count: int = Field(alias="pendingCount")
    approved_count: int = Field(alias="approvedCount")
    pending_action: PendingExpenseAction | None = Field(default=None, alias="pendingAction")


class ExpensePayload(APIModel):
    kind: Literal["expense"] = "expense"
    message: str
    overview: ExpenseOverview
    created: list[dict[str, Any]] | None = None


class ClarificationOption(APIModel):
    workspace: Literal["knowledge", "data", "expense"]
    label: str


class ClarificationPayload(APIModel):
    kind: Literal["clarification"] = "clarification"
    message: str
    options: list[ClarificationOption]


AgentPayload = Annotated[
    KnowledgePayload | DataPayload | ExpensePayload | ClarificationPayload,
    Field(discriminator="kind"),
]


class AssistantResult(APIModel):
    session_id: str = Field(alias="sessionId")
    user_message_id: str | None = Field(default=None, alias="userMessageId")
    message_id: str = Field(alias="messageId")
    workspace: Literal["knowledge", "data", "expense", "clarification"]
    answer: str
    payload: AgentPayload
