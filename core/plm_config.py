"""
PLM REST API Configuration
"""

class PlmConfig:
    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    PRODUCTION_URL = "http://splm.sec.samsung.net/plmapi/broker.do"
    BASE_URL = "http://10.195.55.11:8080/plmapi/broker.do"
    APP_ID = "cpsol_telephony_rag_ai"
    USER_LANG = "EN"
    TIMEOUT = 30

    # ------------------------------------------------------------------
    # Division
    # ------------------------------------------------------------------
    MOBILE_DIVISION = "25"

    # ------------------------------------------------------------------
    # Service Code
    # ------------------------------------------------------------------
    GET_DEFECT_INFO = "plm.tqm.plmif.getWlPlmDefectInfoIf"

    # TODO
    # LIST_ATTACHMENTS = ""
    # DOWNLOAD_ATTACHMENT = ""
    # ADD_COMMENT = ""
    # UPDATE_STATUS = ""
