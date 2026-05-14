import re

# ==========================================
# 공통 정규식
# ==========================================
RE_TIME = re.compile(r'\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}')
RE_TAG = re.compile(r'[VDIWE]\s+([a-zA-Z0-9_\-]+)\s*(?=:)', re.I)

# ==========================================
# Telephony (Call / OOS) 정규식
# ==========================================
TEL_PATTERNS = {
    'CS_START': re.compile(r'(?:RILJ\s+:\s+)?\[\d+\]>\s(?:DIAL|EMERGENCY_DIAL)|(?:RILJ\s+:\s+)?\[UNSL\]<\sUNSOL_CALL_RING|(?:RILJ\s+:\s+)?\<\s*GET_CURRENT_CALLS\s*\{[^}]*INCOMING', re.I),
    'PS_START': re.compile(r'(?:IPF|IPCT).*>\s*(?:createCallProfile)|(?:IPF|IPCT).*onIncomingCall', re.I),
    'CONN_ID': re.compile(r'(?:ImsPhoneConnection|ImsPhoneCallTracker).*telecomCallID:\s*([^\s,]+)', re.I),
    'END_EV': re.compile(r'\[IPCN(\d*)\]>\s*close|\<\s*LAST_CALL_FAIL_CAUSE', re.I),
    'FAIL_EV': re.compile(r'(onCallStartFailed|onCallHoldFailed|onCallResumeFailed)', re.I),
    'REJECT_EV': re.compile(r'IPF.*>\s*reject\s*\{reason:\s*(\w+)', re.I),
    'SST_POLL': re.compile(r'Poll ServiceState done', re.I),
    'IMS_REASON': re.compile(r'ImsReasonInfo\s*::\s*\{(\d+)\s*:\s*(\w+)'),
    'CS_REASON': re.compile(r'LAST_CALL_FAIL_CAUSE.*?causeCode:\s*(\d+)\s+vendorCause:\s*(\d+)')
}

SST_FIELDS = {
    'v_reg': re.compile(r'm?VoiceRegState\s*=\s*([^,\s]+)', re.I),
    'd_reg': re.compile(r'mDataRegState\s*=\s*([^,\s]+)', re.I),
    'rat': re.compile(r'm?RadioTechnology\s*=\s*([^,\s]+)', re.I),
    'op_long': re.compile(r'm?OperatorAlphaLong\s*=\s*([^,\s]+)', re.I),
    'op_short': re.compile(r'm?OperatorAlphaShort\s*=\s*([^,\s]+)', re.I),
    'is_emergency': re.compile(r'm?IsEmergencyOnly\s*=\s*([^,\s]+)', re.I),
    'rej_cause': re.compile(r'm?RejectCause\s*=\s*([^,\s]+)', re.I)
}

# ==========================================
# Diagnostic (Boot, Signal, Net, Crash) 정규식
# ==========================================
DIAG_PATTERNS = {
    'BOOT_EVENT': re.compile(r'^((!@Boot:|!@Boot_SVC|!@Boot_DEBUG).*?)\s+(\d+)\s+(\d+)\s+(\d+)', re.I),
    'SIGNAL_LEVEL': re.compile(r'(\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?\[(\d+)\] EVENT_SIGNAL_LEVEL_INFO_CHANGED - SignalBarInfo\{\s*(.*?)\s*\}'),
    'NETSTAT_IDENT': re.compile(r'ident=\[\{.*?metered=true.*?transports=\{0\}\}\].*?uid=(-\d+|\d+)'),
    'NETSTAT_BYTES': re.compile(r'rb=(\d+)\s+rp=\d+\s+tb=(\d+)'),
    'DNS_FULL': re.compile(r'(?P<time>\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}).*?DNS Requested by\s+\d+,\s*(?P<uid>\d+)\((?P<app_name>[^)]+)\)(?P<rest>.*)'),

    # Radio Power
    'RADIO_REQ': re.compile(r'(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+radio\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<level>[VDIWEFS])\s+RILJ\s*:\s*\[(?P<seq>\d+)\]\s*>\s*RADIO_POWER\s+on\s*=\s*(?P<on>\w+)\s+forEmergencyCall\s*=\s*(?P<for_emergency>\w+)\s+preferredForEmergencyCall\s*=\s*(?P<preferred_emergency>\w+)\s+\[(?P<phone>PHONE\d+)\]'),
    'RADIO_RESP': re.compile(r'(?P<timestamp>\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+radio\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<level>[VDIWEFS])\s+RILJ\s*:\s*\[(?P<seq>\d+)\]\s*<\s*RADIO_POWER\s*(?P<content>.*)'),

    # ANR / Crash
    'PID_LINE': re.compile(r'----- pid (\d+) at ', re.I),
    'CMD_PHONE': re.compile(r'Cmd line:\s+com\.android\.phone', re.I),
    'THREAD_HEADER': re.compile(r'^"(.*?)".*?(?:sysTid|tid)=(\d+)', re.I),
    'LOCK_HELD': re.compile(r'waiting to lock <(.*?)>.*?held by thread (\d+)', re.I),
    'ANR_TRACES': re.compile(r'VM TRACES AT LAST ANR', re.I),
    'OUTGOING': re.compile(r'outgoing transaction (\d+):(\d+) to (\d+):(\d+) code (\d+)', re.I),
    'FATAL_APP': re.compile(r'FATAL EXCEPTION:\s+(\w+)', re.I),
    'FATAL_SYS': re.compile(r'FATAL EXCEPTION IN PROCESS:\s+(\w+)', re.I),
    'PROC_PHONE': re.compile(r'Process:\s+com\.android\.phone', re.I),
    'STACK_LINE': re.compile(r'^\s*(at\s+|Caused\s+by:)', re.I)
}

# ==========================================
# 필터링 상수 모음
# ==========================================
RADIO_POWER_ERRORS = ['GENERIC_FAILURE', 'RADIO_NOT_AVAILABLE', 'REQUEST_NOT_SUPPORTED', 'INVALID_ARGUMENTS', 'INTERNAL_ERR', 'MODEM_ERR', 'FAILURE', 'ERROR']
VALID_TAGS = {'RILD', 'RILD2', 'RILJ', 'IPF', 'IMS', 'VoLTE', 'SST', 'ServiceState', 'SignalStrength', 'ServiceStateTracker', 'ImsPhoneCallTracker', 'ImsPhoneConnection', 'SST-1', 'SST-0'}
COMMON_EXCLUDES = ['keep-alive', 'handlePollStateResultMessage', 'getCarrierNameDisplayBitmask']
PS_EXCLUDE_TAGS = {'RILD', 'RILD2', 'RILJ'}
NETWORK_EXCLUDE_TAGS = {'ImsPhoneConnection', 'ImsPhoneCallTracker'}
RE_HEX_DATA = re.compile(r'([0-9a-fA-F]{2}\s){3,}')

TAG_SPECIFIC_EXCLUDES = {
    'RILD': ['SCREEN_STATE', 'GET_OPERATOR', 'WAKE_LOCK', 'BATTERY_LEVEL', 'nv::', 'ProcessIpcMessageReceived', 'GetIpcMessage', 'IsRegisteredNetworkType', 'ServiceMode', 'SvcMode', 'IpcProtocol GR', 'OEM', 'GetRxData','Signal', 'RSSI', 'Dbm', 'RefreshHandler', '[B]', '3gRove', 'mWeak', 'lte_sig', 'IpcRx', 'DoRoute', 'Onprocessing', '-MGR', 'Request', 'serviceType', 'MakeData', '[*]', 'IpcModem', 'PsRegistration', 'Pdn', 'RRC_STATE', 'RegistrationState', 'UnsolRespFilter', 'Screen', 'hysteresis', 'CellInfo', 'earfcn', 'IsRegistered', 'Rsrp', 'BuildSolicited', 'PhysicalChannel', 'DataCall', 'Interface', 'SSAC', 'RrcState', 'ACTIVITY_INFO', 'BIG_DATA', 'IpcProtocol41', 'ProcessSingleIpcMessageReceived', 'NITZ', 'Location', 'PS_REGISTRATION', 'SetEmergencyState'],
    'RILJ': ['UNSOL_PHYSICAL_CHANNEL_CONFIG', 'SET_SIGNAL_STRENGTH_REPORTING_CRITERIA', 'SET_UNSOLICITED_RESPONSE_FILTER', 'SEND_DEVICE_STATE', 'Sending ack', 'GET_BARRING_INFO', 'UNSOL_RESPONSE_NETWORK_STATE_CHANGED', 'processResponse', 'OPERATOR', 'QUERY_NETWORK_SELECTION_MODE', 'VOICE_REGISTRATION_STATE', 'DATA_REGISTRATION_STATE']
}