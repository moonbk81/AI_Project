"""Agent analytics toolkit."""

from agent_toolkit.call_tools import (
    get_cs_call_analytics,
    get_ps_ims_call_analytics,
)

from agent_toolkit.network_tools import (
    get_network_oos_analytics,
    get_dns_latency_analytics,
    get_radio_power_analytics,
    get_data_stall_and_recovery_analytics,
    get_internet_stall_analytics,
    get_internet_stall_kpi_for_integrated_report,
    get_recent_data_usage_analytics,
    get_datacall_setup_analytics,
)

from agent_toolkit.battery_tools import (
    get_battery_thermal_analytics,
)

from agent_toolkit.crash_tools import (
    get_crash_anr_analytics,
)

from agent_toolkit.binder_tools import (
    get_binder_warning_analytics,
)

from agent_toolkit.satellite_tools import (
    get_ntn_spacex_analytics,
    get_tiantong_satellite_analytics,
)

from agent_toolkit.kpi_tools import (
    get_device_health_kpi,
)

__all__ = [
    "get_cs_call_analytics",
    "get_ps_ims_call_analytics",
    "get_network_oos_analytics",
    "get_dns_latency_analytics",
    "get_radio_power_analytics",
    "get_data_stall_and_recovery_analytics",
    "get_internet_stall_analytics",
    "get_internet_stall_kpi_for_integrated_report",
    "get_recent_data_usage_analytics",
    "get_battery_thermal_analytics",
    "get_crash_anr_analytics",
    "get_binder_warning_analytics",
    "get_ntn_spacex_analytics",
    "get_tiantong_satellite_analytics",
    "get_device_health_kpi",
]