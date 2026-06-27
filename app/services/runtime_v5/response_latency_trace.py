from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


@dataclass
class ResponseLatencyTrace:
    """Internal per-message latency trace.

    This is observability input only. It must not change routing, permissions,
    Runtime behavior, or user-facing text.
    """

    started_at: float = field(default_factory=perf_counter)
    phases: list[dict[str, Any]] = field(default_factory=list)

    def mark(self, phase: str, started_at: float, **metadata: Any) -> int:
        duration_ms = _elapsed_ms(started_at)
        item: dict[str, Any] = {"phase": phase, "duration_ms": duration_ms}
        item.update({key: value for key, value in metadata.items() if value not in (None, "")})
        self.phases.append(item)
        return duration_ms

    def payload(self, **metadata: Any) -> dict[str, Any]:
        total_ms = _elapsed_ms(self.started_at)
        slowest = max(self.phases, key=lambda item: int(item.get("duration_ms") or 0), default={})
        return {
            "available": True,
            "total_ms": total_ms,
            "phases": list(self.phases),
            "slowest_phase": str(slowest.get("phase") or ""),
            "slowest_phase_ms": int(slowest.get("duration_ms") or 0),
            **{key: value for key, value in metadata.items() if value not in (None, "")},
        }


def response_latency_from_bot_timing(timing: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(timing, dict) or not timing.get("available"):
        return {"available": False, "reason": "bot_response_timing_missing"}
    phases = timing.get("phases") if isinstance(timing.get("phases"), list) else []
    return {
        "available": True,
        "total_ms": int(timing.get("total_ms") or 0),
        "phases": list(phases),
        "slowest_phase": str(timing.get("slowest_phase") or ""),
        "slowest_phase_ms": int(timing.get("slowest_phase_ms") or 0),
        "source": "bot_response_timing",
    }


def merge_gateway_latency_trace(
    *,
    diagnostics_snapshot: dict[str, Any],
    gateway_trace: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(diagnostics_snapshot)
    runtime_trace = response_latency_from_bot_timing(
        merged.get("bot_response_timing") if isinstance(merged.get("bot_response_timing"), dict) else {}
    )
    phases: list[dict[str, Any]] = []
    if runtime_trace.get("available"):
        phases.extend(item for item in runtime_trace.get("phases", []) if isinstance(item, dict))
    if isinstance(gateway_trace.get("phases"), list):
        phases.extend(item for item in gateway_trace["phases"] if isinstance(item, dict))
    slowest = max(phases, key=lambda item: int(item.get("duration_ms") or 0), default={})
    try:
        runtime_total = int(runtime_trace.get("total_ms") or 0)
        gateway_total = int(gateway_trace.get("total_ms") or 0)
    except (TypeError, ValueError):
        runtime_total = gateway_total = 0
    merged["response_latency_trace"] = {
        "available": True,
        "runtime_total_ms": runtime_total,
        "gateway_total_ms": gateway_total,
        "total_ms": max(runtime_total, gateway_total),
        "phases": phases,
        "slowest_phase": str(slowest.get("phase") or ""),
        "slowest_phase_ms": int(slowest.get("duration_ms") or 0),
        "route_path": gateway_trace.get("route_path") or merged.get("route_path") or "",
        "reply_channel": gateway_trace.get("reply_channel") or "",
    }
    return merged


def _elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)
