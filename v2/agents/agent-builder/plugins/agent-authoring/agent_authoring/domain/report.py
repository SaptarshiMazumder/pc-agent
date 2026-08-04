"""Report — the result of validating ONE agent: every Finding, plus a verdict.

Pure value. It owns exactly one policy decision — *what counts as failure* — and one rendering
concern: turning findings into the text the model reads. Keeping both here means the tool layer
stays a thin adapter and the rules stay ignorant of presentation.

``ok`` is TRUE when there are no ``error`` findings. Warnings and info do NOT fail validation:
a warning means "this loads but something you wrote is being ignored", and info is forward-looking
guidance (the sandbox tier). Failing on those would train the model to ignore the whole report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .finding import LEVEL_RANK, Finding


@dataclass(frozen=True)
class Report:
    agent_id: str
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """No errors. Warnings/info are advisory by design (see the module docstring)."""
        return not any(f.is_error for f in self.findings)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.level] = out.get(f.level, 0) + 1
        return out

    def as_text(self) -> str:
        """The whole report as the model sees it: verdict line, then worst-first findings."""
        if not self.findings:
            return f"validate_agent '{self.agent_id}': OK — no findings."
        counts = self.counts()
        summary = ", ".join(
            f"{counts[level]} {level}" for level in ("error", "warn", "info") if counts.get(level)
        )
        verdict = "OK" if self.ok else "FAILED"
        lines = [f"validate_agent '{self.agent_id}': {verdict} ({summary})", ""]
        lines += [
            f.as_line()
            for f in sorted(self.findings, key=lambda f: (LEVEL_RANK.get(f.level, 9), f.path))
        ]
        if not self.ok:
            lines += ["", "Fix the [x] items with `write`, then call validate_agent again."]
        return "\n".join(lines)
