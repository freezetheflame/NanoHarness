from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── LLM interaction ──

class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any]


class LLMResponse(BaseModel):
    content: str
    tool_calls: Optional[List[ToolCall]] = None


class AgentMessage(BaseModel):
    role: str  # system, user, assistant, tool
    content: str
    tool_calls: Optional[List[ToolCall]] = None


class StopSignal(BaseModel):
    """Mid-loop evaluation: should the engine stop early?"""
    should_stop: bool = False
    reason: str = ""
    stop_category: str = ""  # "error_loop" | "spinning" | "stagnation" | ""


class EvaluationResult(BaseModel):
    """Post-loop evaluation: did the agent achieve the task?"""
    achieved: bool = False
    confidence: float = 0.0
    explanation: str = ""
    evidence: List[str] = Field(default_factory=list)


class StepResult(BaseModel):
    """单步执行的结果记录"""
    step_id: int
    thought: str
    action: Optional[Dict] = None
    observation: Optional[str] = None
    status: str = "success"  # success, error, terminated
    stop_signal: Optional[StopSignal] = None


# ── Run outcome ──

class RunStatus(str, Enum):
    """How execution ended, independently of whether the goal was achieved."""

    COMPLETED = "completed"
    STOPPED = "stopped"
    EXHAUSTED = "exhausted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(str, Enum):
    """Canonical reason why the engine stopped executing steps."""

    MODEL_TERMINATED = "model_terminated"
    SUBJECT_COMPLETED = "subject_completed"
    EVALUATOR_STOPPED = "evaluator_stopped"
    MAX_STEPS = "max_steps"
    ERROR = "error"
    USER_CANCELLED = "user_cancelled"


class RunResult(BaseModel):
    """Canonical engine result with a compatibility view for legacy reports.

    ``status`` describes execution, while ``evaluation.achieved`` is the sole
    success verdict. The mapping helpers keep existing ``report["summary"]``
    and ``report["trajectory"]`` callers working during migration.
    """

    status: RunStatus
    stop_reason: StopReason
    stop_detail: str = ""
    final_answer: Optional[str] = None
    evaluation: EvaluationResult
    trajectory: List[StepResult] = Field(default_factory=list)
    avg_thought_length: float = 0.0

    @property
    def success(self) -> bool:
        return self.evaluation.achieved

    @property
    def summary(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "total_steps": len(self.trajectory),
            "avg_thought_length": self.avg_thought_length,
            "evaluation": self.evaluation.model_dump(),
            "stop_reason": self.stop_detail or self.stop_reason.value,
            "status": self.status.value,
        }

    def __getitem__(self, key: str) -> Any:
        if key == "summary":
            return self.summary
        if key == "trajectory":
            return [step.model_dump() for step in self.trajectory]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
