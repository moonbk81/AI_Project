"""Presentation contract for the dumpstate private-network snapshot."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PrivateNetworkOverview:
    status: str
    classification: str = "unknown"
    classification_label: str = "판정 불가"
    summary: str = ""
    confidence: str = "low"
    is_private_network: bool = False
    is_private_address: bool = False
    is_vpn_active: bool = False
    is_enterprise_private: bool = False
    is_5g_private_candidate: bool = False
    network_id: Optional[str] = None
    transport: Optional[str] = None
    radio_technology: Optional[str] = None
    interface: Optional[str] = None
    apn: Optional[str] = None
    ssid: Optional[str] = None
    addresses: List[str] = field(default_factory=list)
    private_addresses: List[str] = field(default_factory=list)
    dns_addresses: List[str] = field(default_factory=list)
    gateway: Optional[str] = None
    proxy: Optional[str] = None
    validated: bool = False
    warnings: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    vpn_management: Dict[str, Any] = field(default_factory=dict)


def build_private_network_overview(data: Optional[Dict[str, Any]]) -> PrivateNetworkOverview:
    if not data:
        return PrivateNetworkOverview(status="no_data")
    if data.get("status") != "ok":
        return PrivateNetworkOverview(
            status=str(data.get("status") or "no_data"),
            summary=str(data.get("summary") or ""),
            vpn_management=data.get("vpn_management") or {},
        )

    return PrivateNetworkOverview(
        status="ok",
        classification=str(data.get("classification") or "unknown"),
        classification_label=str(data.get("classification_label") or "판정 불가"),
        summary=str(data.get("summary") or ""),
        confidence=str(data.get("confidence") or "low"),
        is_private_network=bool(data.get("is_private_network")),
        is_private_address=bool(data.get("is_private_address")),
        is_vpn_active=bool(data.get("is_vpn_active")),
        is_enterprise_private=bool(data.get("is_enterprise_private")),
        is_5g_private_candidate=bool(data.get("is_5g_private_candidate")),
        network_id=str(data.get("active_default_network_id") or "") or None,
        transport=data.get("transport"),
        radio_technology=data.get("radio_technology"),
        interface=data.get("interface"),
        apn=data.get("apn"),
        ssid=data.get("ssid"),
        addresses=list(data.get("addresses") or []),
        private_addresses=list(data.get("private_addresses") or []),
        dns_addresses=list(data.get("dns_addresses") or []),
        gateway=data.get("gateway"),
        proxy=data.get("proxy"),
        validated=bool(data.get("validated")),
        warnings=list(data.get("warnings") or []),
        evidence=list(data.get("evidence") or []),
        vpn_management=dict(data.get("vpn_management") or {}),
    )
