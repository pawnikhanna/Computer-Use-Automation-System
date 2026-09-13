from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Locators: HOW we find a control, given no test IDs.
# ---------------------------------------------------------------------------

class LocatorStrategyKind(str, Enum):
    LABEL_TEXT = "label_text"          # "the input in the same row/near text X"
    ROLE_NAME = "role_name"            # ARIA role + accessible name (button "Search")
    TEXT_EXACT = "text_exact"          # exact visible text (for links, buttons)
    CSS_FALLBACK = "css_fallback"      # structural CSS path, last resort
    XPATH_FALLBACK = "xpath_fallback"  # structural XPath, last resort


class LocatorStrategy(BaseModel):
    kind: LocatorStrategyKind
    value: str
    robustness_note: str = Field(
        description="Why this strategy was chosen / how brittle it is expected to be."
    )


class Locator(BaseModel):
    """An ordered list of independent strategies for finding one element.
    Replay tries them in order and records which one succeeded -- that record
    is itself a drift signal over many replays."""
    description: str  
    strategies: list[LocatorStrategy]


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    NAVIGATE = "navigate"
    FILL = "fill"
    SELECT = "select"
    CLICK = "click"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"


class RiskLevel(str, Enum):
    SAFE_REVERSIBLE = "safe_reversible"      # reads, navigation
    RISKY_IRREVERSIBLE = "risky_irreversible"  # writes with no undo (e.g. final confirm)


class Step(BaseModel):
    step_id: str
    action: ActionType
    risk_level: RiskLevel = RiskLevel.SAFE_REVERSIBLE

    # Present for NAVIGATE
    url: Optional[str] = None

    # Present for FILL / SELECT / CLICK / WAIT_FOR / EXTRACT
    target: Optional[Locator] = None

    # For FILL/SELECT: which input PARAMETER's value to use (bound at replay
    # time, not hardcoded) -- this is what makes the artifact reusable rather
    # than a recording of one specific run.
    input_binding: Optional[str] = None

    # For FILL/SELECT actions NOT bound to a parameter: a fixed literal value
    # to use every replay. Never used for anything sensitive -- credentials
    # and identifiers are always parameterized (see guardrails: never persist
    # secrets into artifacts).
    literal_value: Optional[str] = None

    # For EXTRACT: which OUTPUT field this step's extracted value feeds.
    extract_as: Optional[str] = None
    extract_pattern: Optional[str] = Field(
        default=None,
        description="Optional regex to pull a sub-value out of the extracted text, "
                    "e.g. extracting '4231.50' out of '$4231.50'."
    )

    description: str = ""  # human-readable summary of what this step does and why


# ---------------------------------------------------------------------------
# Recoverable conditions -- artifact-level, checked before every step.
# ---------------------------------------------------------------------------

class RecoverableCondition(BaseModel):
    """A known, benign runtime state (e.g. an interstitial notice) that must be
    dismissed before the underlying step can proceed. Checked opportunistically
    before each step rather than being modeled as part of the happy-path flow."""
    name: str
    detect_text: str            # if this text is present on the page...
    dismiss_action: Locator     # ...click/interact with this control to clear it


# ---------------------------------------------------------------------------
# Business outcomes vs. hard failures
# ---------------------------------------------------------------------------

class BusinessOutcome(BaseModel):
    """A legitimate, expected result that is NOT a failure (e.g. 'no such
    member'). Detected by pattern match; returns a typed result to the caller
    instead of raising."""
    name: str
    detect_text: Optional[str] = None
    detect_url_contains: Optional[str] = None
    detect_status_code: Optional[int] = None
    result_shape: dict = Field(
        description="The typed payload returned to the caller for this outcome, "
                    "e.g. {'found': false}."
    )


class Checkpoint(BaseModel):
    """Asserted at the end of the flow to confirm the goal was actually reached,
    rather than assuming the last click worked."""
    kind: Literal["text_present", "url_contains", "element_visible"]
    value: str


# ---------------------------------------------------------------------------
# Typed I/O contract
# ---------------------------------------------------------------------------

class ParamType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    ENUM = "enum"


class ParameterSpec(BaseModel):
    name: str
    type: ParamType
    required: bool = True
    description: str = ""
    enum_values: Optional[list[str]] = None
    example: Optional[str] = None


class OutputSpec(BaseModel):
    name: str
    type: ParamType
    description: str = ""


# ---------------------------------------------------------------------------
# Top-level artifact
# ---------------------------------------------------------------------------

class ArtifactMeta(BaseModel):
    schema_version: str = "1.0"          # version of THIS schema definition
    capability_version: int = 1          # version of THIS specific capability
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    discovered_by_model: str
    discovery_run_id: str                # links back to /evidence/<run_id>/


class Capability(BaseModel):
    """A capability an AI agent can invoke by name with typed args."""
    capability_id: str            # slug, e.g. "lookup_member_savings_balance"
    name: str
    description: str
    meta: ArtifactMeta

    target_base_url: str
    entry_path: str               # where replay starts, e.g. "/members/search"
    requires_auth: bool = True

    input_params: list[ParameterSpec]
    outputs: list[OutputSpec]

    recoverable_conditions: list[RecoverableCondition] = Field(default_factory=list)
    steps: list[Step]
    business_outcomes: list[BusinessOutcome] = Field(default_factory=list)
    success_checkpoint: Checkpoint

    # Multi-tenant seam (unused for now -- see REPORT.md section 4). Keyed by
    # tenant/vendor-variant id; each override can replace specific locators or
    # the base_url without touching the base artifact.
    tenant_overrides: Optional[dict[str, dict]] = None


# ---------------------------------------------------------------------------
# Replay result contract -- the three-way outcome the brief asks for.
# ---------------------------------------------------------------------------

class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    HARD_FAILURE = "hard_failure"
    ESCALATED = "escalated"


class ReplayResult(BaseModel):
    status: ReplayStatus
    capability_id: str
    outputs: dict = Field(default_factory=dict)          # for SUCCESS
    business_outcome_name: Optional[str] = None           # for BUSINESS_OUTCOME
    failure_step_id: Optional[str] = None                 # for HARD_FAILURE
    failure_expected: Optional[str] = None
    failure_observed: Optional[str] = None
    evidence_dir: Optional[str] = None
    escalation_notes: Optional[str] = None
    locator_resolution_log: list[dict] = Field(
        default_factory=list,
        description="Which locator strategy succeeded per step -- drift signal."
    )