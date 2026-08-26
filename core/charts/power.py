"""Power and thermal chart series.

Built from the Chroma metadata frame. Nothing here imports a web framework or
plotly — see `core/charts/__init__.py` for the split.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .common import slice_log_type

# Above this a sensor is worth a second look; drawn as a reference line.
THERMAL_WARNING_C = 40

# All three panels show the worst offenders only.
_TOP_ROWS = 10

# Process names are long enough to push the x labels into each other.
_PROCESS_LABEL_CHARS = 18


@dataclass(frozen=True)
class PowerPanelSection:
    """One of the three panels. `status` is `"ok"` or `"no_data"`."""

    status: str
    frame: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass(frozen=True)
class PowerThermalPanel:
    wakelocks: PowerPanelSection
    thermals: PowerPanelSection
    cpu: PowerPanelSection
    thermal_warning_c: int = THERMAL_WARNING_C


def _numeric(df: pd.DataFrame, column: str) -> pd.DataFrame:
    df = df.copy()
    df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _shorten(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _PROCESS_LABEL_CHARS:
        return value[:_PROCESS_LABEL_CHARS] + "..."
    return value


def _wakelocks(df: pd.DataFrame) -> PowerPanelSection:
    wl_df = slice_log_type(df, "Wakelock_Stat")
    if wl_df.empty:
        return PowerPanelSection(status="no_data")
    # The parser already emits these worst-first.
    return PowerPanelSection(status="ok", frame=_numeric(wl_df, "times").head(_TOP_ROWS))


def _thermals(df: pd.DataFrame) -> PowerPanelSection:
    thermal_df = slice_log_type(df, "Thermal_Stat")
    if thermal_df.empty:
        return PowerPanelSection(status="no_data")

    thermal_df = _numeric(thermal_df, "temperature")
    thermal_df = thermal_df.dropna(subset=["temperature"]).sort_values(
        by="temperature", ascending=False
    )
    return PowerPanelSection(status="ok", frame=thermal_df.head(_TOP_ROWS))


def _cpu(df: pd.DataFrame) -> PowerPanelSection:
    cpu_df = slice_log_type(df, "Cpu_Usage_Stat")
    if cpu_df.empty:
        return PowerPanelSection(status="no_data")

    cpu_df = _numeric(cpu_df, "cpu_percent")
    cpu_df["process_label"] = cpu_df["process"].apply(_shorten)
    return PowerPanelSection(status="ok", frame=cpu_df.head(_TOP_ROWS))


def build_power_thermal_panel(df: pd.DataFrame) -> PowerThermalPanel:
    """Wakelocks, sensor temperatures and CPU hogs, side by side."""
    return PowerThermalPanel(wakelocks=_wakelocks(df), thermals=_thermals(df), cpu=_cpu(df))
