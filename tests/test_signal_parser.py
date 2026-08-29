from parsers.analysis_bucket_builder import AnalysisBucketBuilder
from parsers.diagnostic_parser import SignalParser


def test_signal_parser_uses_registered_cell_signal_strength_lte_as_rsrp_source():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoLte:{mRegistered=YES CellSignalStrengthLte: rssi=-89 rsrp=-120 rsrq=-16 rssnr=0 cqiTableIndex=2147483647 cqi=2147483647 ta=2147483647 level=0 parametersUseForLevel=1 android.telephony.CellConfigLte :{ isEndcAvailable = false }}, CellInfoLte:{mRegistered=NO CellSignalStrengthLte: rssi=-101 rsrp=-134 rsrq=-20 rssnr=2147483647 cqiTableIndex=2147483647 cqi=2147483647 ta=2147483647 level=0 parametersUseForLevel=1 android.telephony.CellConfigLte :{ isEndcAvailable = false }}] [PHONE0]",
        "03-30 20:49:26.000 radio  1793  1793 D SSCtr-0 : [0] EVENT_SIGNAL_LEVEL_INFO_CHANGED - SignalBarInfo{ lteLevel=0 }",
    ]

    history = SignalParser().analyze(lines)

    assert len(history) == 1
    assert history[0]["level"] == 0
    assert history[0]["details"]["LTE"]["RSRP"] == "-120 dBm"
    assert history[0]["details"]["LTE"]["RSRQ"] == "-16 dB"
    assert history[0]["details"]["LTE"]["SINR"] == "0.0 dB"


def test_signal_parser_uses_cell_signal_strength_nr_as_rsrp_source():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoNr:{mRegistered=YES CellSignalStrengthNr: ssRsrp=-105 ssRsrq=-12 ssSinr=18 csiRsrp=2147483647 csiRsrq=2147483647 csiSinr=2147483647 level=2 parametersUseForLevel=1}] [PHONE0]",
        "03-30 20:49:26.000 radio  1793  1793 D SSCtr-0 : [0] EVENT_SIGNAL_LEVEL_INFO_CHANGED - SignalBarInfo{ nrLevel=2 }",
    ]

    history = SignalParser().analyze(lines)

    assert len(history) == 1
    assert history[0]["level"] == 2
    assert history[0]["details"]["NR"]["RSRP"] == "-105 dBm"
    assert history[0]["details"]["NR"]["RSRQ"] == "-12 dB"
    assert history[0]["details"]["NR"]["SINR"] == "18 dB"


def test_signal_bucket_includes_cell_signal_strength_lines():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoLte:{mRegistered=YES CellSignalStrengthLte: rssi=-89 rsrp=-120 rsrq=-16 rssnr=0 level=0}] [PHONE0]",
    ]

    buckets = AnalysisBucketBuilder(add_context_window=False).build(lines)

    assert buckets["signal"] == lines
