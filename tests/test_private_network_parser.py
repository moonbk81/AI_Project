import json

from fastapi.testclient import TestClient

import backend.main as backend_main
from log_orchestrator import LogOrchestrator
from parsers.private_network_parser import PrivateNetworkParser
from prepare_rag_payload import RagPayloadBuilder
from rag_builders.network_builder import build_private_network_payloads


def _agent(
    network_id="110",
    ni="MOBILE[NR] CONNECTED extra: cmwap",
    transport="CELLULAR",
    interface="rmnet15",
    address="10.1.37.213/24",
    capabilities="INTERNET&NOT_RESTRICTED&TRUSTED&NOT_VPN",
    suffix="",
):
    return (
        f"NetworkAgentInfo{{network{{{network_id}}} ni{{{ni}}} "
        f"lp{{{{InterfaceName: {interface} LinkAddresses: [ {address} ] "
        "DnsAddresses: [ /120.80.80.80,/221.5.88.88 ] "
        f"Routes: [ 0.0.0.0/0 -> 10.1.37.1 {interface} mtu 1400 ] "
        f"{suffix}}}}} nc{{[ Transports: {transport} Capabilities: {capabilities} "
        "Specifier: <test> UnderlyingNetworks: Null]}}}"
    )


def test_carrier_private_address_is_not_enterprise_or_vpn():
    lines = [
        "Active default network: 110\n",
        _agent(suffix="HttpProxy: [cmwap] 8080") + "\n",
        "DUMP OF SERVICE vpn_management:\n",
        "VPNs:\n",
        "0: [Legacy VPN]\n",
        "Active vpn type: -1\n",
        "NetworkCapabilities: [ Transports: VPN TransportInfo: <VpnTransportInfo{type=-1, sessionId=null}> UnderlyingNetworks: Null]\n",
        "Mode changed: lockdown=false alwaysOn=false\n",
        "--------- 0.01s was the duration of dumpsys vpn_management\n",
    ]

    result = PrivateNetworkParser().analyze(lines)

    assert result["classification"] == "carrier_private_path"
    assert result["private_addresses"] == ["10.1.37.213/24"]
    assert result["proxy"] == "cmwap:8080"
    assert result["is_private_address"] is True
    assert result["is_private_network"] is False
    assert result["is_vpn_active"] is False
    assert result["vpn_management"]["placeholder_present"] is True
    assert result["vpn_management"]["active_hint"] is False


def test_wifi_oem_private_false_is_local_lan_only():
    line = _agent(
        network_id="103",
        ni="WIFI CONNECTED extra: ",
        transport="WIFI",
        interface="wlan0",
        address="192.168.2.59/24",
        capabilities="INTERNET&NOT_RESTRICTED&TRUSTED&NOT_VPN&VALIDATED",
        suffix='TransportInfo: <SSID: "office", Restricted: false, OEM private: false>',
    )

    result = PrivateNetworkParser().analyze(["Active default network: 103\n", line])

    assert result["classification"] == "local_private_lan"
    assert result["ssid"] == "office"
    assert result["validated"] is True
    assert result["is_private_network"] is False


def test_only_the_active_default_vpn_agent_counts_as_active_vpn():
    normal = _agent(network_id="100", address="100.64.1.2/30")
    vpn = _agent(
        network_id="120",
        ni="VPN CONNECTED extra: corp",
        transport="VPN",
        interface="tun0",
        address="10.20.0.2/24",
        capabilities="INTERNET&NOT_RESTRICTED&TRUSTED",
    )

    result = PrivateNetworkParser().analyze(["Active default network: 120", normal, vpn])

    assert result["classification"] == "active_vpn"
    assert result["is_private_network"] is True
    assert result["is_vpn_active"] is True
    assert result["interface"] == "tun0"


def test_network_offer_enterprise_does_not_change_active_agent_verdict():
    lines = [
        "NetworkOffer [ Transports: CELLULAR Capabilities: ENTERPRISE ]",
        "Active default network: 110",
        _agent(),
    ]

    result = PrivateNetworkParser().analyze(lines)

    assert result["classification"] == "carrier_private_path"
    assert result["is_enterprise_private"] is False


def test_private_network_result_enters_rag_payload_without_raw_dump():
    result = PrivateNetworkParser().analyze(["Active default network: 110", _agent()])

    payloads = build_private_network_payloads({"private_network": result}, "sample_report.json")

    assert len(payloads) == 1
    assert payloads[0]["metadata"]["log_type"] == "Private_Network_Status"
    assert payloads[0]["metadata"]["classification"] == "carrier_private_path"
    assert "NetworkAgentInfo network=110" in payloads[0]["document"]
    assert len(payloads[0]["document"]) < 3000


def test_fastapi_exposes_private_network_chart_and_raw_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    parsed = PrivateNetworkParser().analyze(["Active default network: 110", _agent()])
    (result_dir / "sample_private_network.json").write_text(
        json.dumps(parsed, ensure_ascii=False), encoding="utf-8"
    )
    client = TestClient(backend_main.app)

    chart = client.get("/charts/private-network", params={"source_file": "sample_payload.json"})
    artifact = client.get("/results/sample/private_network")

    assert chart.status_code == 200
    assert chart.json()["series"]["classification"] == "carrier_private_path"
    assert chart.json()["series"]["network_id"] == "110"
    assert artifact.status_code == 200
    assert artifact.json()["schema_version"] == "private_network_v1"


def test_orchestrator_report_artifact_and_full_payload_are_connected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "dumpstate.log"
    log.write_text("\n".join(["Active default network: 110", _agent()]), encoding="utf-8")
    report = tmp_path / "result" / "dumpstate_report.json"
    report.parent.mkdir()

    assert LogOrchestrator(str(log)).run_batch(str(report)) is True
    report_data = json.loads(report.read_text(encoding="utf-8"))
    artifact_data = json.loads(
        (tmp_path / "result" / "dumpstate_private_network.json").read_text(encoding="utf-8")
    )

    assert report_data["private_network"]["classification"] == "carrier_private_path"
    assert artifact_data == report_data["private_network"]

    RagPayloadBuilder(str(report)).build_payload("dumpstate_payload.json")
    payload = json.loads(
        (tmp_path / "payloads" / "dumpstate_payload.json").read_text(encoding="utf-8")
    )
    private_rows = [
        row for row in payload["payloads"]
        if row["metadata"].get("log_type") == "Private_Network_Status"
    ]
    assert len(private_rows) == 1
    assert private_rows[0]["metadata"]["classification"] == "carrier_private_path"
