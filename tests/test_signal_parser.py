from parsers.analysis_bucket_builder import AnalysisBucketBuilder
from parsers.diagnostic_parser import SignalParser


def test_signal_parser_uses_registered_cell_signal_strength_lte_as_rsrp_source():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoLte:{mRegistered=YES CellSignalStrengthLte: rssi=-89 rsrp=-120 rsrq=-16 rssnr=0 cqiTableIndex=2147483647 cqi=2147483647 ta=2147483647 level=0 parametersUseForLevel=1 android.telephony.CellConfigLte :{ isEndcAvailable = false }}, CellInfoLte:{mRegistered=NO CellSignalStrengthLte: rssi=-101 rsrp=-134 rsrq=-20 rssnr=2147483647 cqiTableIndex=2147483647 cqi=2147483647 ta=2147483647 level=0 parametersUseForLevel=1 android.telephony.CellConfigLte :{ isEndcAvailable = false }}] [PHONE0]",
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
    ]

    history = SignalParser().analyze(lines)

    assert len(history) == 1
    assert history[0]["level"] == 2
    assert history[0]["details"]["NR"]["RSRP"] == "-105 dBm"
    assert history[0]["details"]["NR"]["RSRQ"] == "-12 dB"
    assert history[0]["details"]["NR"]["SINR"] == "18 dB"


def test_signal_parser_accepts_framework_nr_format_with_braces_and_spaced_fields():
    lines = [
        "03-30 22:49:53.631  1000  4377  4377 I EPDG-0 [CellularProfiler]: onSignalStrengthsChanged: SignalStrength:{mCdma=Invalid,mGsm=Invalid,mWcdma=Invalid,mTdscdma=Invalid,mLte=Invalid,mNr=CellSignalStrengthNr:{ csiRsrp = 2147483647 csiRsrq = 2147483647 csiCqiTableIndex = 2147483647 csiCqiReport = [] ssRsrp = -110 ssRsrq = -12 ssSinr = 5 level = 1 parametersUseForLevel = 0 timingAdvance = 2147483647 },SignalBarInfo{ nrLevel=4 },rat=20,primary=CellSignalStrengthNr}",
    ]

    history = SignalParser().analyze(lines)

    assert len(history) == 1
    assert history[0]["rat"] == "NR"
    assert history[0]["level"] == 1
    assert history[0]["details"]["NR"]["RSRP"] == "-110 dBm"
    assert history[0]["details"]["NR"]["RSRQ"] == "-12 dB"
    assert history[0]["details"]["NR"]["SINR"] == "5 dB"


def test_signal_parser_keeps_lte_nr_level_when_rsrp_is_unknown():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoLte:{mRegistered=YES CellSignalStrengthLte: rssi=-89 rsrp=2147483647 rsrq=2147483647 rssnr=2147483647 level=3}, CellInfoNr:{mRegistered=YES CellSignalStrengthNr: ssRsrp=2147483647 ssRsrq=2147483647 ssSinr=2147483647 level=2}] [PHONE0]",
    ]

    history = SignalParser().analyze(lines)

    assert [(event["rat"], event["level"]) for event in history] == [("LTE", 3), ("NR", 2)]
    assert history[0]["details"]["LTE"]["RSRP"] == "Unknown"
    assert history[1]["details"]["NR"]["RSRP"] == "Unknown"


def test_signal_parser_uses_cell_signal_strength_wcdma_and_gsm_as_level_sources():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoWcdma:{mRegistered=YES CellSignalStrengthWcdma: ss=17 ber=99 rscp=-93 ecno=-8 level=3}, CellInfoGsm:{mRegistered=YES CellSignalStrengthGsm: rssi=-87 ber=0 mTa=2147483647 level=2}] [PHONE1]",
    ]

    history = SignalParser().analyze(lines)

    assert [(event["rat"], event["level"], event["slot"]) for event in history] == [
        ("WCDMA", 3, "1"),
        ("GSM", 2, "1"),
    ]
    assert history[0]["details"]["WCDMA"]["RSCP"] == "-93 dBm"
    assert history[1]["details"]["GSM"]["RSSI"] == "-87 dBm"


def test_signal_parser_ignores_network_signal_strength_handler_and_level_events():
    lines = [
        "03-30 20:49:25.879 radio  1906  1924 D RILD    : NetworkSignalStrengthHandler - SignalStrength: [4, C:(2147483647, 2147483647), E:(2147483647, 2147483647, -1), G:(99, 99), W:(99, 99, 255, 255), T:(2147483647), L:(13, 87, 5, 300, 11), N:(-92, -12)]",
        "03-30 20:49:26.000 radio  1793  1793 D SSCtr-0 : [0] EVENT_SIGNAL_LEVEL_INFO_CHANGED - SignalBarInfo{ lteLevel=4 nrLevel=4 }",
    ]

    history = SignalParser().analyze(lines)

    assert history == []


def test_signal_bucket_includes_cell_signal_strength_lines():
    lines = [
        "03-30 20:49:25.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoLte:{mRegistered=YES CellSignalStrengthLte: rssi=-89 rsrp=-120 rsrq=-16 rssnr=0 level=0}] [PHONE0]",
        "03-30 20:49:26.879 radio  4156  4254 D RILJ    : [UNSL]< UNSOL_CELL_INFO_LIST [CellInfoWcdma:{mRegistered=YES CellSignalStrengthWcdma: ss=17 rscp=-93 ecno=-8 level=3}, CellInfoGsm:{mRegistered=YES CellSignalStrengthGsm: rssi=-87 ber=0 level=2}] [PHONE0]",
    ]

    buckets = AnalysisBucketBuilder(add_context_window=False).build(lines)

    assert buckets["signal"] == lines


def test_signal_bucket_excludes_network_signal_strength_handler_and_level_events():
    lines = [
        "03-30 20:49:25.879 radio  1906  1924 D RILD    : NetworkSignalStrengthHandler - SignalStrength: [4, L:(13, 87, 5, 300, 11), N:(-92, -12)]",
        "03-30 20:49:26.000 radio  1793  1793 D SSCtr-0 : [0] EVENT_SIGNAL_LEVEL_INFO_CHANGED - SignalBarInfo{ lteLevel=4 nrLevel=4 }",
    ]

    buckets = AnalysisBucketBuilder(add_context_window=False).build(lines)

    assert buckets["signal"] == []
