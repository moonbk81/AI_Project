"""Parse the active private-network state from Android dumpstate output.

VPN listeners and provider offers describe what Android could satisfy, not
what currently carries traffic. Verdicts here are anchored to ``Active default
network`` and that network's ``NetworkAgentInfo``. The vpn_management block is
kept only as supporting state so its permanent Legacy VPN placeholder is not
reported as an active tunnel.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


class PrivateNetworkParser:
    SCHEMA_VERSION = "private_network_v1"
    _ACTIVE_DEFAULT_RE = re.compile(r"Active default network:\s*(\d+|none)", re.I)
    _AGENT_RE = re.compile(r"NetworkAgentInfo\{network\{(\d+)\}")
    _PRIVATE_5G_RE = re.compile(
        r"(?:\bSNPN\b|\bNPN\b|\bCAG\b|\bCSG\b|non[- ]public network|private 5g)", re.I
    )
    _TUNNEL_IFACE_RE = re.compile(r"^(?:tun|tap|wg|ipsec|ppp)", re.I)

    def analyze(self, lines: Iterable[str]) -> Dict[str, Any]:
        active_ids: List[str] = []
        agents: Dict[str, Dict[str, Any]] = {}
        vpn_lines: List[str] = []
        in_vpn_dump = False

        # The orchestrator has already loaded the file. Keep only relevant
        # single lines; no dumpstate-sized raw block reaches report or payload.
        for raw_line in lines:
            line = str(raw_line).strip()
            if not line:
                continue

            active = self._ACTIVE_DEFAULT_RE.search(line)
            if active:
                active_ids.append(active.group(1).lower())

            agent = self._AGENT_RE.search(line)
            if agent:
                agents[agent.group(1)] = self._parse_agent(line, agent.group(1))

            if "DUMP OF SERVICE vpn_management" in line:
                in_vpn_dump = True
                vpn_lines = []
                continue
            if in_vpn_dump:
                if line.startswith("---------") and "dumpsys vpn_management" in line:
                    in_vpn_dump = False
                    continue
                if len(vpn_lines) < 40:
                    vpn_lines.append(line[:1000])

        active_id = active_ids[-1] if active_ids else None
        if active_id == "none":
            active_id = None
        active = agents.get(active_id or "")
        vpn_state = self._parse_vpn_management(vpn_lines)

        if not active_id:
            return self._empty_result("no_active_default", vpn_state)
        if not active:
            result = self._empty_result("active_network_details_missing", vpn_state)
            result["active_default_network_id"] = active_id
            result["summary"] = f"기본 네트워크 {active_id}의 NetworkAgentInfo를 찾지 못했습니다."
            return result

        classification, label, private_kind = self._classify(active)
        is_vpn = classification == "active_vpn"
        is_private = classification in {
            "active_vpn",
            "oem_private_network",
            "enterprise_private_network",
            "private_5g_candidate",
        }
        warnings = []
        if vpn_state["placeholder_present"] and not is_vpn:
            warnings.append("Legacy VPN 항목은 있으나 활성 기본 VPN NetworkAgent는 확인되지 않았습니다.")
        if active["has_private_address"] and not is_private:
            warnings.append("사설 IP는 VPN/기업 사설망을 의미하지 않으며 LAN 또는 통신사 NAT일 수 있습니다.")

        evidence = [
            f"Active default network: {active_id}",
            self._agent_evidence(active),
        ]
        if active.get("proxy"):
            evidence.append(f"HttpProxy: {active['proxy']}")

        return {
            "schema_version": self.SCHEMA_VERSION,
            "status": "ok",
            "classification": classification,
            "classification_label": label,
            "private_network_kind": private_kind,
            "is_private_network": is_private,
            "is_private_address": active["has_private_address"],
            "is_vpn_active": is_vpn,
            "is_enterprise_private": classification in {
                "oem_private_network", "enterprise_private_network"
            },
            "is_5g_private_candidate": classification == "private_5g_candidate",
            "confidence": "high" if classification != "private_5g_candidate" else "medium",
            "active_default_network_id": active_id,
            "transport": active.get("transport"),
            "radio_technology": active.get("radio_technology"),
            "connection_state": active.get("connection_state"),
            "interface": active.get("interface"),
            "apn": active.get("apn"),
            "ssid": active.get("ssid"),
            "addresses": active.get("addresses", []),
            "private_addresses": active.get("private_addresses", []),
            "dns_addresses": active.get("dns_addresses", []),
            "gateway": active.get("gateway"),
            "proxy": active.get("proxy"),
            "validated": active.get("validated", False),
            "restricted": active.get("restricted"),
            "oem_private": active.get("oem_private"),
            "capabilities": active.get("capabilities", []),
            "underlying_networks": active.get("underlying_networks"),
            "vpn_management": vpn_state,
            "summary": self._summary(label, active),
            "warnings": warnings,
            "evidence": evidence,
        }

    def save_ui_report(self, output_dir: str, base_name: str, result: Dict[str, Any]) -> str:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{base_name}_private_network.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
        return path

    def _parse_agent(self, line: str, network_id: str) -> Dict[str, Any]:
        ni = self._group(line, r"\bni\{([^}]*)\}") or ""
        transport_text = self._group(line, r"Transports:\s*([A-Z_|]+)") or ""
        capability_text = self._group(
            line,
            r"Capabilities:\s*(.*?)(?:\s+LinkUpBandwidth|\s+LinkDnBandwidth|\s+Specifier:|\s+TransportInfo:|\s+SignalStrength:|\s+OwnerUid:|\s+AdminUids:|\s+SubscriptionIds:|\s+UnderlyingNetworks:|\]\})",
        ) or ""
        capabilities = [item for item in capability_text.split("&") if item]
        addresses = self._csv_group(line, r"LinkAddresses:\s*\[\s*([^\]]*)\]")
        dns_addresses = [value.lstrip("/") for value in self._csv_group(line, r"DnsAddresses:\s*\[\s*([^\]]*)\]")]
        private_addresses = [value for value in addresses if self._is_private_ip(value)]
        interface = self._group(line, r"InterfaceName:\s*([^\s}\]]+)")
        gateway = self._group(line, r"(?:0\.0\.0\.0/0|::/0)\s*->\s*([^\s,]+)")
        proxy_host = self._group(line, r"HttpProxy:\s*\[([^\]]+)\]")
        proxy_port = self._group(line, r"HttpProxy:\s*\[[^\]]+\]\s*(\d+)")
        extra = (self._group(ni, r"extra:\s*(.*)$") or "").strip()

        return {
            "network_id": network_id,
            "transport": transport_text.replace("_", " ").replace("|", ", ") or None,
            "transports": [value for value in transport_text.split("|") if value],
            "radio_technology": self._group(ni, r"MOBILE\[([^\]]+)\]"),
            "connection_state": self._group(ni, r"\b(CONNECTED|CONNECTING|DISCONNECTED|SUSPENDED)\b"),
            "interface": interface,
            "apn": extra or None,
            "ssid": self._group(line, r'SSID:\s*"([^"]*)"'),
            "addresses": addresses,
            "private_addresses": private_addresses,
            "has_private_address": bool(private_addresses),
            "dns_addresses": dns_addresses,
            "gateway": gateway,
            "proxy": f"{proxy_host}:{proxy_port}" if proxy_host and proxy_port else proxy_host,
            "capabilities": capabilities,
            "validated": "VALIDATED" in capabilities,
            "restricted": self._bool_group(line, r"Restricted:\s*(true|false)"),
            "oem_private": self._bool_group(line, r"OEM private:\s*(true|false)"),
            "underlying_networks": self._group(line, r"UnderlyingNetworks:\s*([^\]\}]+)"),
            "private_5g_signal": bool(self._PRIVATE_5G_RE.search(line)),
        }

    def _classify(self, active: Dict[str, Any]) -> Tuple[str, str, str]:
        transports = set(active["transports"])
        capabilities = set(active["capabilities"])
        iface = active.get("interface") or ""
        is_vpn = "VPN" in transports or (
            "NOT_VPN" not in capabilities and bool(self._TUNNEL_IFACE_RE.search(iface))
        )
        if is_vpn:
            return "active_vpn", "활성 VPN", "vpn"
        if active.get("oem_private") is True:
            return "oem_private_network", "OEM 사설망", "enterprise"
        if "ENTERPRISE" in capabilities:
            return "enterprise_private_network", "기업용 사설망", "enterprise"
        if active.get("private_5g_signal") and active.get("radio_technology") == "NR":
            return "private_5g_candidate", "5G 사설망 후보", "5g_npn"
        if "CELLULAR" in transports and (active["has_private_address"] or active.get("proxy")):
            return "carrier_private_path", "통신사 사설 주소 경로", "carrier_nat"
        if transports.intersection({"WIFI", "ETHERNET"}) and active["has_private_address"]:
            return "local_private_lan", "로컬 사설 LAN", "local_lan"
        return "public_or_unclassified", "일반/공용 네트워크", "none"

    def _parse_vpn_management(self, lines: List[str]) -> Dict[str, Any]:
        text = "\n".join(lines)
        placeholder = "[Legacy VPN]" in text
        vpn_type = self._group(text, r"Active vpn type:\s*(-?\d+)")
        session_id = self._group(text, r"sessionId=([^,}\s]+)")
        underlying = self._group(text, r"UnderlyingNetworks:\s*([^\]\n]+)")
        active_hint = bool(
            (vpn_type is not None and vpn_type != "-1")
            or (session_id and session_id.lower() != "null")
            or (underlying and underlying.strip().lower() != "null")
        )
        return {
            "entry_present": bool(lines),
            "placeholder_present": placeholder,
            "active_hint": active_hint,
            "vpn_type": int(vpn_type) if vpn_type is not None else None,
            "session_id": None if not session_id or session_id.lower() == "null" else session_id,
            "underlying_networks": underlying.strip() if underlying else None,
            "lockdown": self._bool_group(text, r"lockdown=(true|false)"),
            "always_on": self._bool_group(text, r"alwaysOn=(true|false)"),
        }

    def _empty_result(self, status: str, vpn_state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "status": status,
            "classification": "unknown",
            "classification_label": "판정 불가",
            "private_network_kind": "unknown",
            "is_private_network": False,
            "is_private_address": False,
            "is_vpn_active": False,
            "is_enterprise_private": False,
            "is_5g_private_candidate": False,
            "confidence": "low",
            "active_default_network_id": None,
            "transport": None,
            "radio_technology": None,
            "connection_state": None,
            "interface": None,
            "apn": None,
            "ssid": None,
            "addresses": [],
            "private_addresses": [],
            "dns_addresses": [],
            "gateway": None,
            "proxy": None,
            "validated": False,
            "restricted": None,
            "oem_private": None,
            "capabilities": [],
            "underlying_networks": None,
            "vpn_management": vpn_state,
            "summary": "활성 기본 네트워크를 확인할 수 없습니다.",
            "warnings": [],
            "evidence": [],
        }

    @staticmethod
    def _summary(label: str, active: Dict[str, Any]) -> str:
        parts = [label]
        if active.get("transport"):
            parts.append(active["transport"])
        if active.get("radio_technology"):
            parts.append(active["radio_technology"])
        if active.get("apn"):
            parts.append(f"APN {active['apn']}")
        if active.get("interface"):
            parts.append(f"인터페이스 {active['interface']}")
        if active.get("private_addresses"):
            parts.append(f"사설 주소 {', '.join(active['private_addresses'])}")
        return " · ".join(parts)

    @staticmethod
    def _agent_evidence(active: Dict[str, Any]) -> str:
        capabilities = "&".join(active.get("capabilities", [])) or "none"
        return (
            f"NetworkAgentInfo network={active['network_id']} transport={active.get('transport') or 'unknown'} "
            f"state={active.get('connection_state') or 'unknown'} interface={active.get('interface') or 'unknown'} "
            f"capabilities={capabilities}"
        )[:1000]

    @staticmethod
    def _group(text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.I | re.S)
        return match.group(1).strip() if match else None

    @classmethod
    def _csv_group(cls, text: str, pattern: str) -> List[str]:
        value = cls._group(text, pattern)
        if not value:
            return []
        return [item.strip().lstrip("/") for item in value.split(",") if item.strip()]

    @classmethod
    def _bool_group(cls, text: str, pattern: str) -> Optional[bool]:
        value = cls._group(text, pattern)
        return None if value is None else value.lower() == "true"

    @staticmethod
    def _is_private_ip(address: str) -> bool:
        host = address.split("%", 1)[0].split("/", 1)[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        # Link-local does not turn a global IPv6 network into a private path.
        return ip.is_private and not ip.is_link_local
