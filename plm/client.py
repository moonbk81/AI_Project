import json
from typing import Any, Dict, List

import requests

from core.plm_config import PlmConfig
from plm.models import Defect


class PlmClient:
    def __init__(
        self,
        single_id: str,
        base_url: str = PlmConfig.BASE_URL,
        app_id: str = PlmConfig.APP_ID,
        user_lang: str = PlmConfig.USER_LANG,
        timeout: int = PlmConfig.TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.single_id = single_id
        self.app_id = app_id
        self.user_lang = user_lang
        self.timeout = timeout
        self.session = requests.Session()

    def _call(
        self,
        service_code: str,
        params: Dict[str, Any],
        method: str = "GET",
    ) -> Dict[str, Any]:
        query = {
            "singleId": self.single_id,
            "appId": self.app_id,
            "serviceCode": service_code,
            "userLang": self.user_lang,
            "param": json.dumps(params, ensure_ascii=False),
        }

        url = f"{self.base_url}/broker.do"
        method = method.upper()

        if method == "GET":
            response = self.session.get(url, params=query, timeout=self.timeout)
        elif method == "POST":
            response = self.session.post(url, data=query, timeout=self.timeout)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        response.raise_for_status()

        try:
            return response.json()
        except ValueError:
            return {"success": False, "raw": response.text}

    def get_defect_info(
        self,
        defect_codes: List[str],
        division_code: str = PlmConfig.MOBILE_DIVISION,
    ) -> List[Defect]:
        response = self._call(
            service_code=PlmConfig.GET_DEFECT_INFO,
            params={
                "divisionCode": division_code,
                "defectCode": ",".join(defect_codes),
            },
            method="GET",
        )

        if not response.get("success", False):
            raise RuntimeError(f"Failed to get defect information: {response}")

        return [
            Defect(
                defect_id=item.get("defectId", ""),
                defect_code=item.get("defectCode", ""),
                title=item.get("plmTitle", ""),
                status=item.get("openSubStatus", ""),
                reason=item.get("reason", ""),
            )
            for item in response.get("defectList", [])
        ]

    def call_service(
        self,
        service_code: str,
        params: Dict[str, Any],
        method: str = "GET",
    ) -> Dict[str, Any]:
        return self._call(
            service_code=service_code,
            params=params,
            method=method,
        )
