"""The offline fixture must generate actual searchable domain evidence."""
import json
from pathlib import Path

from core.analysis_pipeline import run_analysis_core
from plm import local_test
from plm.log_pipeline import LOG_READY, extract_logs_from_attachments


def test_local_attachment_produces_searchable_ims_failures_and_recovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = list(extract_logs_from_attachments(
        local_test.SAMPLE_FILES["P260711-LOCAL01"],
        lambda **kwargs: local_test.download_file(kwargs["title"]),
    ))
    files = []
    for event in events:
        if event.kind == LOG_READY:
            path = tmp_path / Path(event.filename).name
            path.write_bytes(event.content)
            files.append(str(path))
    assert files

    class RecordingEngine:
        def ingest_file(self, path, **kwargs):
            self.payload = json.loads(Path(path).read_text())
            return {"added": len(self.payload["payloads"]), "errors": 0, "skipped": 0}

    engine = RecordingEngine()
    result = run_analysis_core(files, False, "", "", engine, owner="local.agent", defect_code="P260711-LOCAL01")
    report = json.loads(Path(result.report_path).read_text())
    methods = {event["method_code"] for event in report["ims_sip_data"]}
    assert {"REGISTER", "408 Request Timeout", "503 Service Unavailable", "200 OK"} <= methods
    sip = [row for row in engine.payload["payloads"] if row["metadata"]["log_type"] == "IMS_SIP_Message"]
    assert len(sip) == 6
