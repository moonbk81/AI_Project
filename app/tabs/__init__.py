"""Streamlit tab renderer exports."""

from .benchmark_tab import render_benchmark_tab
from .boot_tab import render_boot_tab
from .chat_tab import render_chat_tab
from .dashboard_tab import render_dashboard_tab
from .internet_tab import render_internet_tab
from .satellite_tab import render_satellite_tab
from .knowledge_tab import render_knowledge_tab
from .plm_tab import render_plm_section_tab

__all__ = [
    "render_benchmark_tab",
    "render_boot_tab",
    "render_chat_tab",
    "render_dashboard_tab",
    "render_internet_tab",
    "render_satellite_tab",
    "render_knowledge_tab",
    "render_plm_section_tab",
]