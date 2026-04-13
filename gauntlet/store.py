from __future__ import annotations

from pathlib import Path

import yaml

from .models import Finding, Plan


class PlanStore:
    """Persists attack plans to disk and reuses them across runs.

    Plans are stored at ``{root}/{weapon_id}/{plan_name}.yaml``.  When the
    runner generates plans for a weapon, any plan whose name matches a stored
    entry is replaced with the stored version instead of keeping the freshly
    generated one.  Genuinely new plans are saved to disk for future runs.
    """

    def __init__(self, root: str | Path = ".gauntlet/plans") -> None:
        self._root = Path(root)

    def load(self, weapon_id: str) -> dict[str, Plan]:
        """Return all stored plans for a weapon, keyed by plan name."""
        weapon_dir = self._root / weapon_id
        if not weapon_dir.exists():
            return {}
        plans: dict[str, Plan] = {}
        for path in sorted(weapon_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text())
            plans[path.stem] = Plan(**data)
        return plans

    def save(self, plan: Plan) -> None:
        """Persist a plan to disk. ``plan.weapon_id`` must be set."""
        if not plan.weapon_id:
            raise ValueError(f"Plan {plan.name!r} has no weapon_id; cannot persist.")
        weapon_dir = self._root / plan.weapon_id
        weapon_dir.mkdir(parents=True, exist_ok=True)
        path = weapon_dir / f"{plan.name}.yaml"
        path.write_text(yaml.dump(plan.model_dump(), sort_keys=False, allow_unicode=True))

    def deduplicate(self, plans: list[Plan], weapon_id: str) -> list[Plan]:
        """Replace any plan whose name matches a stored plan with the stored version.

        New plans (no match in the store) are saved and returned as-is with
        ``weapon_id`` stamped on them.
        """
        stored = self.load(weapon_id)
        result: list[Plan] = []
        for plan in plans:
            if plan.name in stored:
                result.append(stored[plan.name])
            else:
                stamped = plan.model_copy(update={"weapon_id": weapon_id})
                self.save(stamped)
                result.append(stamped)
        return result


class FindingsStore:
    """Persists findings to disk indexed by weapon ID.

    Findings are stored at ``{root}/{weapon_id}/{issue}.yaml``.  This enables
    knowledge accumulation across runs: successful attacks, surprising behaviors,
    and confirmed failures are all keyed to the weapon that produced them.
    """

    def __init__(self, root: str | Path = ".gauntlet/findings") -> None:
        self._root = Path(root)

    def load(self, weapon_id: str) -> list[Finding]:
        """Return all stored findings for a weapon."""
        weapon_dir = self._root / weapon_id
        if not weapon_dir.exists():
            return []
        findings: list[Finding] = []
        for path in sorted(weapon_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text())
            findings.append(Finding(**data))
        return findings

    def save(self, finding: Finding) -> None:
        """Persist a finding to disk. ``finding.weapon_id`` must be set."""
        if not finding.weapon_id:
            raise ValueError(f"Finding {finding.issue!r} has no weapon_id; cannot persist.")
        weapon_dir = self._root / finding.weapon_id
        weapon_dir.mkdir(parents=True, exist_ok=True)
        path = weapon_dir / f"{finding.issue}.yaml"
        path.write_text(yaml.dump(finding.model_dump(), sort_keys=False, allow_unicode=True))

    def save_all(self, findings: list[Finding]) -> None:
        """Persist multiple findings, skipping any without a weapon_id."""
        for finding in findings:
            if finding.weapon_id:
                self.save(finding)
