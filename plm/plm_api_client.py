"""
PLM Defect REST API Client

This module provides a Python client for interacting with the Samsung PLM
(Product Lifecycle Management) Defect REST API.

API Guide: PLM Defect Rest API Guide_20260424.xlsx
"""

import requests
import json
import re
import sys
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field, asdict
from enum import Enum


class DivisionCode(str, Enum):
    """Division codes"""
    MOBILE = "25"
    NETWORK = "26"
    VD = "11"
    LIV = "14"


class ChangeType(str, Enum):
    """Change types for defect operations"""
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    MODIFY = "MODIFY"
    MODIFY_TRACKING_INFO = "MODIFY_TRACKING_INFO"
    DRAFT_TO_OPEN = "DRAFT_TO_OPEN"
    REJECT = "REJECT"
    CLOSE = "CLOSE"
    CANCEL = "CANCEL"
    SAVE = "S"
    MODIFY_COMMENT = "M"
    DELETE_COMMENT = "D"


class DefectCategory(str, Enum):
    """Defect categories"""
    HW = "HW"
    SW = "SW"
    MW = "MW"


class ImportanceLevel(str, Enum):
    """Importance/Priority levels"""
    A = "A"
    B = "B"
    C = "C"


class OccurrenceRate(str, Enum):
    """Occurrence rates"""
    ALWAYS = "Always"
    SOMETIMES = "Sometimes"
    ONCE = "Once"


class DefectStatus(str, Enum):
    """Defect status"""
    OPEN = "Open"
    RESOLVE = "Resolve"
    CLOSE = "Close"


class ProjectType(str, Enum):
    """Project/Model types"""
    PRE = "PRE"  # Preceding
    BASIC = "BASIC"  # Basic Comm.
    DEV = "DEV"  # Set
    SW = "SW"  # SW Product
    SUPPORT = "SUPPORT"  # Support Project
    ITEM = "ITEM"  # Maintenance
    MFG = "MFG"  # Manufacturing Model
    ETC = "ETC"  # Separate Test
    SWREL = "SWREL"  # SW Dev


class Phase(str, Enum):
    """Development phases"""
    CA = "CA"
    ER1 = "ER1"
    ER2 = "ER2"
    BC = "BC"
    DV = "DV"
    PV = "PV"
    PR = "PR"
    SR = "SR"
    COMPLETE = "Complete"
    DEVELOPMENT = "Development"
    SWDEVELOP = "SWDEVELOP"


class RejectType(str, Enum):
    """Reject types/reasons"""
    PROBLEM_NOT_RESOLVED = "문제점 해결 안됨"
    INSUFFICIENT_ANALYSIS = "원인/분석 불충분"
    WRONG_VERSION = "해결버전 오입력"
    CANNOT_MAINTAIN_STATUS = "현상태 유지 불가"
    SIDE_EFFECT = "Side Effect"
    ETC = "기타"


@dataclass
class DefectInfo:
    """Response for defect information"""
    defectId: str
    defectCode: str
    plmPriority: Optional[str] = None
    mainOwnerId: Optional[str] = None
    mainOwnerName: Optional[str] = None
    plmStatus: Optional[str] = None
    plmTitle: Optional[str] = None
    content: Optional[str] = None
    reason: Optional[str] = None
    countermeasure: Optional[str] = None
    createUser: Optional[str] = None
    createDate: Optional[str] = None
    updateDate: Optional[str] = None


@dataclass
class DefectRegistrationRequest:
    """Request payload for defect registration"""
    divisionCode: str
    systemCode: str
    changeType: str
    refObjectName: str  # Project/Model name
    refObjectType: str  # Project/Model type
    externalDefectId: str
    defectCategory: str  # HW, SW, MW
    createUser: str
    title: str
    inChargeUser: str  # In-charge user (first is main owner)
    Content: str  # Problem description
    importance: str  # A, B, C
    occurRateType: str  # Always, Sometimes, Once
    occurPhase: str  # Development phase
    testUnit: str
    testItem: str
    functionBlock: str
    detailFunctionclass: str

    # Optional fields
    reappearancePath: Optional[str] = None
    forecastResult: Optional[str] = None
    occurCount: Optional[str] = None
    occurTryTotal: Optional[str] = None
    testCategory: Optional[str] = None
    classification: Optional[str] = None
    occurType: Optional[str] = None
    defectType: Optional[str] = None
    failureType: Optional[str] = None
    detailProblemType: Optional[str] = None
    testCaseYn: Optional[str] = None
    testCaseId: Optional[str] = None
    swVersion: Optional[str] = None
    hwVersion: Optional[str] = None
    reviewDept: Optional[str] = None
    reviewResult: Optional[str] = None
    reviewerId: Optional[str] = None
    isDevVerify: Optional[str] = None
    docAttachedYn: Optional[str] = None
    gatingYn: Optional[str] = None
    contentsName: Optional[str] = None
    contentsVer: Optional[str] = None
    ContentsEtc: Optional[str] = None
    modelNumber: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class DefectModifyRequest:
    """Request payload for defect modification"""
    divisionCode: str
    systemCode: str
    changeType: str
    createUser: str
    defectId: str
    defectCode: str

    # Optional fields
    defectCategory: Optional[str] = None
    swVersion: Optional[str] = None
    importance: Optional[str] = None
    functionBlock: Optional[str] = None
    detailFunctionclass: Optional[str] = None
    title: Optional[str] = None
    inChargeUser: Optional[str] = None
    Content: Optional[str] = None
    reappearancePath: Optional[str] = None
    forecastResult: Optional[str] = None
    occurRateType: Optional[str] = None
    occurCount: Optional[str] = None
    occurTryTotal: Optional[str] = None
    testUnit: Optional[str] = None
    testCategory: Optional[str] = None
    testItem: Optional[str] = None
    classification: Optional[str] = None
    occurType: Optional[str] = None
    failureType: Optional[str] = None
    detailProblemType: Optional[str] = None
    testCaseYn: Optional[str] = None
    testCaseId: Optional[str] = None
    defectType: Optional[str] = None
    hwVersion: Optional[str] = None
    reviewDept: Optional[str] = None
    reviewResult: Optional[str] = None
    reviewerId: Optional[str] = None
    gatingYn: Optional[str] = None
    contentsName: Optional[str] = None
    contentsVer: Optional[str] = None
    ContentsEtc: Optional[str] = None
    modelNumber: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class CommentRegistrationRequest:
    """Request payload for comment registration"""
    divisionCode: str
    systemCode: str
    defectComment: str
    createUser: str
    changeType: str = ChangeType.SAVE.value

    # Either defectId or defectCode must be provided
    defectId: Optional[str] = None
    defectCode: Optional[str] = None
    externalDefectId: Optional[str] = None
    defectCommentId: Optional[str] = None
    isCommentEditorYn: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class APIResponse:
    """API response structure"""
    status: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> 'APIResponse':
        """Create APIResponse from JSON"""
        return cls(
            status=data.get('status', {}),
            result=data.get('result', {})
        )

    def is_success(self) -> bool:
        """Check if API call was successful"""
        status_code = self.status.get('code', '')
        return status_code == 'PLM_API_00'

    def get_error_message(self) -> str:
        """Get error message if present"""
        return self.status.get('message', 'Unknown error')


class PLMDefectAPIClient:
    """Client for PLM Defect REST API"""

    def __init__(
        self,
        base_url: str,
        knox_id: str,
        app_id: str,
        user_lang: str = "en",
        disable_proxy: bool = True
    ):
        """
        Initialize PLM API Client

        Args:
            base_url: Base URL for PLM API (e.g., http://10.195.55.11:8080/plmapi/broker.do)
            knox_id: Knox Portal ID for authentication
            app_id: Application ID
            user_lang: User language (default: 'en')
            disable_proxy: Whether to disable proxy (default: True)
        """
        self.base_url = base_url
        self.knox_id = knox_id
        self.app_id = app_id
        self.user_lang = user_lang
        self.disable_proxy = disable_proxy

        self.proxies = {'http': None, 'https': None} if disable_proxy else None
        self.session = requests.Session()

    def _build_request_data(
        self,
        service_code: str,
        param: Dict[str, Any]
    ) -> Dict[str, str]:
        """Build request data dictionary"""
        return {
            'singleId': self.knox_id,
            'appId': self.app_id,
            'userLang': self.user_lang,
            'serviceCode': service_code,
            'param': json.dumps(param)
        }

    def _make_request(
        self,
        service_code: str,
        param: Dict[str, Any],
        method: str = 'POST'
    ) -> APIResponse:
        """Make API request"""
        data = self._build_request_data(service_code, param)

        try:
            if method.upper() == 'GET':
                response = self.session.get(
                    self.base_url,
                    params=data,
                    proxies=self.proxies
                )
            else:  # POST
                response = self.session.post(
                    self.base_url,
                    data=data,
                    proxies=self.proxies
                )

            response.raise_for_status()

            # Clean response text first to remove control characters
            text = response.text

            # Remove all control characters including newlines inside JSON strings
            # Replace common problematic characters with spaces
            text = text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
            # Remove other control characters
            text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
            # Clean up multiple spaces
            text = re.sub(r' +', ' ', text)

            # Parse JSON from cleaned text
            try:
                result = json.loads(text)
            except json.JSONDecodeError as e:
                print(f"[DEBUG] Raw response: {response.text[:500]}", file=sys.stderr)
                print(f"[DEBUG] Cleaned response: {text[:500]}", file=sys.stderr)
                raise e

            return APIResponse.from_json(result)

        except requests.exceptions.RequestException as e:
            raise PLMAPIException(f"API request failed: {str(e)}")
        except json.JSONDecodeError as e:
            raise PLMAPIException(f"Failed to parse response JSON: {str(e)}")

    def get_defect_info(
        self,
        division_code: str,
        defect_codes: Optional[List[str]] = None,
        defect_ids: Optional[List[str]] = None,
        system_code: Optional[str] = None
    ) -> APIResponse:
        """
        Get defect information

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            defect_codes: List of defect codes (up to 99)
            defect_ids: List of defect IDs (alternative to defect_codes)
            system_code: System code (optional, only for MX)

        Returns:
            APIResponse with defect list
        """
        param = {
            'divisionCode': division_code
        }

        if defect_codes:
            param['defectCode'] = ','.join(defect_codes)
        elif defect_ids:
            param['defectId'] = ','.join(defect_ids)
        else:
            raise ValueError("Either defect_codes or defect_ids must be provided")

        if system_code:
            param['systemCode'] = system_code

        return self._make_request(
            'plm.tqm.plmif.getWlPlmDefectInfoIf',
            param,
            method='POST'
        )

    def register_defect(self, request: DefectRegistrationRequest) -> APIResponse:
        """
        Register a new defect

        Args:
            request: DefectRegistrationRequest object

        Returns:
            APIResponse with defect ID and code
        """
        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT',
            request.to_dict()
        )

    def modify_defect(self, request: DefectModifyRequest) -> APIResponse:
        """
        Modify an existing defect

        Args:
            request: DefectModifyRequest object

        Returns:
            APIResponse with modification status
        """
        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT',
            request.to_dict()
        )

    def resolve_defect(
        self,
        division_code: str,
        system_code: str,
        defect_code: str,
        reason: str,
        countermeasure: str,
        change_type: str = ChangeType.SAVE.value
    ) -> APIResponse:
        """
        Resolve/provide solution for a defect

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            system_code: System code
            defect_code: Defect case code (e.g., P180101-00001)
            reason: Reason for the defect
            countermeasure: Countermeasure/solution
            change_type: 'S'=New, 'A'=Append, 'T'=Temporary

        Returns:
            APIResponse with resolution status
        """
        param = {
            'divisionCode': division_code,
            'systemCode': system_code,
            'defectCode': defect_code,
            'changeType': change_type,
            'reason': reason,
            'countermeasure': countermeasure
        }

        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT_SOL',
            param
        )

    def reject_resolution(
        self,
        division_code: str,
        system_code: str,
        defect_code: str,
        reject_type: str,
        reject_comment: str,
        reject_user: str
    ) -> APIResponse:
        """
        Reject a defect resolution

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            system_code: System code
            defect_code: Defect case code
            reject_type: Reason for rejection
            reject_comment: Comment on rejection
            reject_user: Knox ID of rejecting user

        Returns:
            APIResponse with rejection status
        """
        param = {
            'divisionCode': division_code,
            'systemCode': system_code,
            'changeType': ChangeType.REJECT.value,
            'defectCode': defect_code,
            'rejectType': reject_type,
            'rejectComment': reject_comment,
            'rejectUser': reject_user
        }

        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT',
            param
        )

    def close_defect(
        self,
        division_code: str,
        system_code: str,
        defect_code: str
    ) -> APIResponse:
        """
        Close a defect

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            system_code: System code
            defect_code: Defect case code

        Returns:
            APIResponse with close status
        """
        param = {
            'divisionCode': division_code,
            'systemCode': system_code,
            'changeType': ChangeType.CLOSE.value,
            'defectCode': defect_code
        }

        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT',
            param
        )

    def register_comment(self, request: CommentRegistrationRequest) -> APIResponse:
        """
        Register/modify/delete a comment on a defect

        Args:
            request: CommentRegistrationRequest object
                    changeType: 'S'=Save, 'M'=Modify, 'D'=Delete

        Returns:
            APIResponse with comment ID
        """
        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT_CMT',
            request.to_dict()
        )

    def draft_to_open(
        self,
        division_code: str,
        system_code: str,
        defect_code: str,
        create_user: str,
        external_defect_id: Optional[str] = None,
        defect_id: Optional[str] = None,
        review_dept: Optional[str] = None,
        test_unit: Optional[str] = None,
        model_number: Optional[str] = None
    ) -> APIResponse:
        """
        Move defect from Draft to Open status

        Args:
            division_code: Division code (25=Mobile)
            system_code: System code (e.g., TANK, IAP)
            defect_code: Defect case code
            create_user: Knox ID of creator
            external_defect_id: External system defect ID (Mandatory for TANK)
            defect_id: PLM defect ID
            review_dept: Review department (Mandatory for IAP)
            test_unit: Test unit name (Mandatory for IAP)
            model_number: Model number (Mandatory for IAP)

        Returns:
            APIResponse with status
        """
        param = {
            'divisionCode': division_code,
            'systemCode': system_code,
            'changeType': ChangeType.DRAFT_TO_OPEN.value,
            'defectCode': defect_code,
            'createUser': create_user
        }

        if external_defect_id:
            param['externalDefectId'] = external_defect_id
        if defect_id:
            param['defectId'] = defect_id
        if review_dept:
            param['reviewDept'] = review_dept
        if test_unit:
            param['testUnit'] = test_unit
        if model_number:
            param['modelNumber'] = model_number

        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT',
            param
        )

    def cancel_defect(
        self,
        division_code: str,
        system_code: str,
        defect_code: str,
        cancel_comment: str
    ) -> APIResponse:
        """
        Cancel a defect

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            system_code: System code
            defect_code: Defect case code
            cancel_comment: Comment for cancellation

        Returns:
            APIResponse with cancellation status
        """
        param = {
            'divisionCode': division_code,
            'systemCode': system_code,
            'changeType': ChangeType.CANCEL.value,
            'defectCode': defect_code,
            'cancelComment': cancel_comment
        }

        return self._make_request(
            'IF_OUTER_TO_PLM_DEFECT_CANCEL',
            param
        )

    def get_defect_list(
        self,
        division_code: str,
        main_owner_id: str,
        status: str = "open",
        reg_date: str = None,
        search_type: str = "main"
    ) -> APIResponse:
        """
        Get defect list by main owner

        Args:
            division_code: Division code (e.g., "25" for Mobile)
            main_owner_id: Main owner's Knox ID (comma-separated for multiple)
            status: Status filter (Draft, Open, resolve, close) - default "open"
            reg_date: From register date of defect (format: YYYYMMDD) - if None, uses current year
            search_type: Search criteria (REG: register, MAIN: main owner, SUB: sub owner) - default "main"

        Returns:
            APIResponse with defect list
        """
        from datetime import datetime

        # If regDate not provided, use Jan 1 of current year
        if reg_date is None:
            reg_date = datetime.now().strftime("%Y0101")

        param = {
            'divisionCode': division_code,
            'mainOwnerId': main_owner_id,
            'status': status,
            'regDate': reg_date,
            'searchType': search_type
        }

        return self._make_request(
            'plm.tqm.plmif.getDefectByOwnerIf',
            param
        )

    def get_defect_history(
        self,
        division_code: str,
        defect_codes: Optional[List[str]] = None,
        defect_ids: Optional[List[str]] = None
    ) -> APIResponse:
        """
        Get defect history

        Args:
            division_code: Division code
            defect_codes: List of defect codes
            defect_ids: List of defect IDs

        Returns:
            APIResponse with history information
        """
        param = {
            'divisionCode': division_code
        }

        if defect_codes:
            param['defectCode'] = ','.join(defect_codes)
        elif defect_ids:
            param['defectId'] = ','.join(defect_ids)
        else:
            raise ValueError("Either defect_codes or defect_ids must be provided")

        return self._make_request(
            'plm.tqm.plmif.getWlPlmDefectHistoryIf',
            param
        )

    def reassign_main_owner(
        self,
        division_code: str,
        defect_code: str,
        new_owner_id: str,
        system_code: str
    ) -> APIResponse:
        """
        Reassign main owner of defect

        Args:
            division_code: Division code
            defect_code: Defect case code
            new_owner_id: New main owner's Knox ID
            system_code: System code

        Returns:
            APIResponse with reassignment status
        """
        param = {
            'divisionCode': division_code,
            'defectCode': defect_code,
            'newOwnerId': new_owner_id,
            'systemCode': system_code
        }

        return self._make_request(
            'REASSIGN_PLM_DEFECT_CHARGER',
            param
        )

    def get_file_list(
        self,
        division_code: str,
        defect_code: str,
        attach_type: str = 'OP_DEFECT_ATTACH'
    ) -> APIResponse:
        """
        Get file list from defect

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            defect_code: Defect case code (e.g., P170517-00003)
            attach_type: Attachment type - 'OP_DEFECT_ATTACH' (defect files),
                        'OP_DEFECT_COMMENT' (comment files),
                        'OP_DEFECT_RESOLVE' (solution files)

        Returns:
            APIResponse with file list
        """
        param = {
            'divisionCode': division_code,
            'moduleCode': attach_type,
            'code': defect_code
        }

        return self._make_request(
            'GET_DOC_LIST',
            param
        )

    def download_file(
        self,
        division_code: str,
        doc_id: str,
        title: str,
        file_id: str
    ) -> Dict[str, Any]:
        """
        Download file from defect

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            doc_id: Document ID (from file list response)
            title: File title (from file list response)
            file_id: File ID (from file list response)

        Returns:
            Dictionary with 'success' and 'data' keys. 'data' contains the file binary content.
        """
        # File download uses a different endpoint: /fileapi/getFile.do
        download_url = self.base_url.replace('/plmapi/broker.do', '/fileapi/getFile.do')

        param = {
            'divisionCode': division_code,
            'docId': doc_id,
            'title': title,
            'fileId': file_id
        }

        data = {
            'singleId': self.knox_id,
            'appId': self.app_id,
            'userLang': self.user_lang,
            'serviceCode': 'GET_FILE',
            'param': json.dumps(param)
        }

        try:
            # File download returns binary stream, not JSON
            response = self.session.get(
                download_url,
                params=data,
                proxies=self.proxies,
                stream=True
            )

            response.raise_for_status()

            # Check if response is JSON (error) or binary (file)
            content_type = response.headers.get('content-type', '').lower()

            if 'application/json' in content_type:
                # Error response - parse JSON
                try:
                    result = response.json()
                    status = result.get('status', {})
                    return {
                        'success': False,
                        'message': status.get('message', 'Download failed'),
                        'data': None
                    }
                except:
                    return {
                        'success': False,
                        'message': f'Invalid response: {response.text[:100]}',
                        'data': None
                    }
            else:
                # Binary file response
                file_content = response.content
                if file_content and len(file_content) > 0:
                    return {
                        'success': True,
                        'data': file_content,
                        'size': len(file_content)
                    }
                else:
                    return {
                        'success': False,
                        'message': 'Empty file content received',
                        'data': None
                    }

        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'message': f'API request failed: {str(e)}',
                'data': None
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'message': f'Error: {str(e)}',
                'data': None
            }

    def upload_file(
        self,
        division_code: str,
        defect_id: str,
        file_path: str,
        module_code: str = 'OP_DEFECT_ATTACH'
    ) -> APIResponse:
        """
        Upload file to defect (up to 2GB)

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            defect_id: Defect object ID (returned from defect registration API)
            file_path: Path to file to upload
            module_code: Module code - 'OP_DEFECT_ATTACH' (defect files),
                        'OP_DEFECT_COMMENT' (comment files),
                        'OP_DEFECT_RESOLVE' (solution files)

        Returns:
            APIResponse with file upload status
        """
        # File upload requires multipart form data
        param = {
            'divisionCode': division_code,
            'moduleCode': module_code,
            'requestedId': defect_id
        }

        # File upload uses a different endpoint
        upload_url = self.base_url.replace('/plmapi/broker.do', '/fileapi/createPLMDocument.do')

        data = {
            'singleId': self.knox_id,
            'appId': self.app_id,
            'userLang': self.user_lang,
            'serviceCode': 'CREATE_FILE',
            'divisionCode': division_code,
            'param': json.dumps(param)
        }

        try:
            with open(file_path, 'rb') as f:
                files = {'uploadFile': f}
                response = self.session.post(
                    upload_url,
                    data=data,
                    files=files,
                    proxies=self.proxies
                )

            response.raise_for_status()
            result = response.json()
            return APIResponse.from_json(result)

        except (OSError, IOError) as e:
            raise PLMAPIException(f"File operation failed: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise PLMAPIException(f"API request failed: {str(e)}")

    def get_defect_code_list(self) -> APIResponse:
        """
        Get list of available defect codes

        Returns:
            APIResponse with defect code list
        """
        param = {}
        return self._make_request(
            'GET_DEFECT_CODE',
            param
        )


class PLMAPIException(Exception):
    """Exception for PLM API errors"""
    pass
