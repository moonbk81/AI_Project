"""Battery / thermal payload builders."""

from rag_builders.common import append_payload, source_file_name

def build_battery_payloads(report_data, input_file):
    rag_payload = []

    if "battery_stats" in report_data:
        append_payload(
            rag_payload,
            f"### [Type: Battery_Drain_Report]\n- data: {report_data['battery_stats']}",
            {
                "log_type": "Battery_Drain_Report",
                "source_file": source_file_name(input_file)
            }
        )

    battery_thermal = report_data.get("battery_thermal_stats", {})

    if "thermal_stats" in battery_thermal:
        for thermal in battery_thermal["thermal_stats"]:
            meta = {
                "source_file": source_file_name(input_file),
                "log_type": "Thermal_Stat",
                "sensor": thermal.get("sensor", ""),
                "temperature": thermal.get("temperature", 0.0)
            }
            text_content = (
                f"기기 온도 기록: {meta['sensor']} 센서의 온도가 "
                f"{meta['temperature']}도로 측정되었습니다."
            )
            append_payload(rag_payload, text_content, meta)

    if "wakelock_stats" in battery_thermal:
        for wl in battery_thermal["wakelock_stats"]:
            meta = {
                "source_file": source_file_name(input_file),
                "log_type": "Wakelock_Stat",
                "app_name": wl.get("app_name", "Unknown"),
                "duration": wl.get("duration", ""),
                "times": wl.get("times", 0)
            }
            text_content = (
                f"Wakelock(배터리 점유) 기록: {meta['app_name']} 앱이 단말기가 잠들지 못하도록 "
                f"{meta['times']}회 깨웠으며, 총 {meta['duration']} 동안 배터리를 강제 소모시켰습니다."
            )
            append_payload(rag_payload, text_content, meta)

    if "cpu_usage_stats" in report_data:
        for cpu in report_data["cpu_usage_stats"]:
            proc = cpu.get("process", "Unknown").lstrip("/")
            pct = float(cpu.get("cpu_percent", 0.0))
            append_payload(
                rag_payload,
                f"[CPU 점유율] 프로세스명: {proc}, 점유율: {pct}%",
                {
                    "log_type": "Cpu_Usage_Stat",
                    "process": proc,
                    "cpu_percent": pct
                }
            )

    return rag_payload