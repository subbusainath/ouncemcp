"""Agent task suites: run a model against a server and judge the outcome (spec EVAL-3
to EVAL-7, DISC-6; proposal 0019). See ``suite`` for the YAML format, ``model`` for the
model protocol and the replay model, ``runner`` and ``report``."""

from ouncemcp.eval.agent.model import Model, ReplayModel, ToolCall, Turn
from ouncemcp.eval.agent.report import EvalReport, compare
from ouncemcp.eval.agent.runner import RunRecord, run_suite
from ouncemcp.eval.agent.suite import Suite, SuiteError, load_suite

__all__ = [
    "EvalReport",
    "Model",
    "ReplayModel",
    "RunRecord",
    "Suite",
    "SuiteError",
    "ToolCall",
    "Turn",
    "compare",
    "load_suite",
    "run_suite",
]
