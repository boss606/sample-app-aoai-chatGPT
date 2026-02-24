"""
Per-state configuration for data ingestion pipeline.

Supports multiple states (California, Texas, etc.) with same source types.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class StateConfig:
    """
    Configuration for a single jurisdiction/state.

    Attributes:
        state_name: Full name (e.g., california, texas).
        state_code: 2-3 letter abbreviation (e.g., cal, tx).
        jurisdiction: Short code (e.g., CA, TX).
        sources: List of source identifiers to run.
        court_codes: Court identifiers for CourtListener bulk (e.g., cal for CA Supreme).
        output_dir: Local output directory for generated JSONs.
        root_dir: Project root for resolving paths.
    """

    state_name: str
    state_code: str
    jurisdiction: str
    sources: List[str] = field(default_factory=list)
    court_codes: List[str] = field(default_factory=list)
    output_dir: Optional[Path] = None
    root_dir: Optional[Path] = None

    def __post_init__(self):
        if self.output_dir is None and self.root_dir:
            self.output_dir = self.root_dir / "scripts" / "data_ingestion" / self.state_name / "output"
        if self.output_dir:
            self.output_dir = Path(self.output_dir)


def get_state_config(state_name: str, root_dir: Optional[Path] = None) -> StateConfig:
    """
    Get configuration for a state. Extensible for future states.

    Args:
        state_name: State identifier (e.g., california, texas).
        root_dir: Project root. Defaults to parent of scripts.

    Returns:
        StateConfig for the requested state.
    """
    if root_dir is None:
        root_dir = Path(__file__).resolve().parents[3]

    state_name_lower = state_name.lower()

    configs: Dict[str, StateConfig] = {
        "california": StateConfig(
            state_name="california",
            state_code="cal",
            jurisdiction="CA",
            sources=[
                "california_law",
                "california_rules_of_court",
                "court_forms",
                "courtlistener",
            ],
            court_codes=["cal"],
            root_dir=root_dir,
        ),
        "florida": StateConfig(
            state_name="florida",
            state_code="fla",
            jurisdiction="FL",
            sources=["courtlistener"],
            court_codes=["fla"],
            root_dir=root_dir,
        ),
        "illinois": StateConfig(
            state_name="illinois",
            state_code="ill",
            jurisdiction="IL",
            sources=["courtlistener"],
            court_codes=["ill"],
            root_dir=root_dir,
        ),
        "new_york": StateConfig(
            state_name="new_york",
            state_code="ny",
            jurisdiction="NY",
            sources=["courtlistener"],
            court_codes=["ny"],
            root_dir=root_dir,
        ),
        "texas": StateConfig(
            state_name="texas",
            state_code="tex",
            jurisdiction="TX",
            sources=["courtlistener"],
            court_codes=["tex"],
            root_dir=root_dir,
        ),
    }

    if state_name_lower in configs:
        cfg = configs[state_name_lower]
        if root_dir:
            cfg.root_dir = Path(root_dir)
            cfg.output_dir = Path(root_dir) / "scripts" / "data_ingestion" / state_name_lower / "output"
        return cfg

    raise ValueError(f"Unknown state: {state_name}. Available: {list(configs.keys())}")
