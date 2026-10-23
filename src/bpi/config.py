"""Paths and business settings shared by every stage of the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml


def _repo() -> Path:
    """Where config/, contracts/, semantic/ and warehouse/ live: BPI_REPO, else the source checkout."""
    if os.environ.get("BPI_REPO"):
        return Path(os.environ["BPI_REPO"])
    src = Path(__file__).resolve().parents[2]
    return src if (src / "config").exists() else Path.cwd()


REPO = _repo()


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def truth(self) -> Path:
        # Only the evaluation reads this. Models and the warehouse never see it.
        return self.root / "data" / "truth"

    @property
    def warehouse(self) -> Path:
        return self.root / "data" / "warehouse.duckdb"

    @property
    def registry(self) -> Path:
        return self.root / "out" / "registry"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def powerbi(self) -> Path:
        return self.root / "out" / "powerbi"


def paths() -> Paths:
    return Paths(Path(os.environ.get("BPI_HOME", REPO)))


@dataclass(frozen=True)
class Guardrails:
    """Limits a price recommendation may never cross. Owned by Global Pricing Strategy."""

    min_margin: dict[str, float]  # floor: (price - cost) / price, per service line
    max_move_vs_quote: float  # recommendation stays within +/- this share of the rep's price
    max_above_list: float  # never above list price by more than this share
    deal_desk_lines: tuple[str, ...]  # lines priced by the deal desk, never auto-recommended


@dataclass(frozen=True)
class Settings:
    as_of: date
    train_end: date  # quotes decided before this date train the models
    test_end: date
    cost_of_capital: float  # yearly, for the cost of payment terms beyond the standard
    standard_terms_days: int
    guardrails: Guardrails
    drift_psi_alert: float
    calibration_ece_alert: float
    leakage_z: float
    max_slope_se: float
    extra: dict = field(default_factory=dict)


def load_settings(path: Path | None = None) -> Settings:
    path = path or REPO / "config" / "pricing.yaml"
    raw = yaml.safe_load(path.read_text())
    g = raw["guardrails"]
    return Settings(
        as_of=raw["as_of"],
        train_end=raw["train_end"],
        test_end=raw["test_end"],
        cost_of_capital=raw["cost_of_capital"],
        standard_terms_days=raw["standard_terms_days"],
        guardrails=Guardrails(
            min_margin=g["min_margin"],
            max_move_vs_quote=g["max_move_vs_quote"],
            max_above_list=g["max_above_list"],
            deal_desk_lines=tuple(g["deal_desk_lines"]),
        ),
        drift_psi_alert=raw["monitoring"]["psi_alert"],
        calibration_ece_alert=raw["monitoring"]["ece_alert"],
        leakage_z=raw["leakage"]["rep_z"],
        max_slope_se=raw["price_tests"]["max_slope_se"],
        extra=raw,
    )
