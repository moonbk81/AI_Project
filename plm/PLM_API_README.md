# PLM Defect REST API Client

Python client for Samsung PLM (Product Lifecycle Management) Defect REST API.

**API Guide Reference**: PLM Defect Rest API Guide_20260424.xlsx

## Overview

This module provides a comprehensive Python wrapper for the PLM Defect REST API, enabling automated management of defects including:

- **Defect Registration**: Create new defects with full details
- **Defect Retrieval**: Query defect information with multiple filtering options
- **Defect Modification**: Update existing defect information
- **Defect Resolution**: Provide solutions and track resolutions
- **Status Management**: Draft → Open, Resolve, Reject, Close, Cancel
- **Comment Management**: Add, modify, and delete comments
- **File Management**: Upload, download, and list files
- **Owner Management**: Reassign main owners
- **History Tracking**: Query defect history

## Features

- **Type-safe**: Enums for all constants (DivisionCode, ChangeType, etc.)
- **Dataclass-based requests**: Clean, validated request objects
- **Error handling**: Custom exceptions with meaningful messages
- **Flexible parameters**: Optional fields for maximum compatibility
- **Proxy support**: Built-in proxy control
- **Comprehensive documentation**: Docstrings for all methods

## Installation

```bash
pip install requests
```

## Quick Start

### 1. Initialize Client

```python
from plm_api_client import PLMDefectAPIClient

client = PLMDefectAPIClient(
    base_url="http://10.195.55.11:8080/plmapi/broker.do",  # PLM server URL
    knox_id="your_knox_id",                                 # Your Knox ID
    app_id="PLM_API_TEST",                                  # Your App ID
    user_lang="en"
)
```

### 2. Get Defect Information

```python
from plm_api_client import DivisionCode

response = client.get_defect_info(
    division_code=DivisionCode.MOBILE.value,
    defect_codes=["P190404-00007", "P191014-00003"]
)

if response.is_success():
    defects = response.result['defectList']
    for defect in defects:
        print(f"{defect['defectCode']}: {defect['plmTitle']}")
else:
    print(f"Error: {response.get_error_message()}")
```

### 3. Register a Defect

```python
from plm_api_client import (
    DefectRegistrationRequest, DivisionCode, ChangeType,
    DefectCategory, ImportanceLevel, OccurrenceRate, Phase
)

request = DefectRegistrationRequest(
    divisionCode=DivisionCode.MOBILE.value,
    systemCode="PLM_API_TEST",
    changeType=ChangeType.DRAFT.value,
    refObjectName="Galaxy S24",
    refObjectType="MFG",  # Manufacturing Model
    externalDefectId="EXT_001",
    defectCategory=DefectCategory.SW.value,
    createUser="your_knox_id",
    title="API display issue",
    inChargeUser="owner_id",
    Content="Display freezes when scrolling fast",
    importance=ImportanceLevel.B.value,
    occurRateType=OccurrenceRate.SOMETIMES.value,
    occurPhase=Phase.DV.value,
    testUnit="S/W Engineering",
    testItem="Functional Test",
    functionBlock="Display",
    detailFunctionclass="Screen Rendering",
)

response = client.register_defect(request)
if response.is_success():
    print(f"Defect registered: {response.result['defectCode']}")
```

### 4. Add a Comment

```python
from plm_api_client import CommentRegistrationRequest

request = CommentRegistrationRequest(
    divisionCode=DivisionCode.MOBILE.value,
    systemCode="PLM_API_TEST",
    defectCode="P190404-00007",
    defectComment="Also reproduced on version 1.2.3",
    createUser="your_knox_id"
)

response = client.register_comment(request)
```

### 5. Resolve a Defect

```python
response = client.resolve_defect(
    division_code=DivisionCode.MOBILE.value,
    system_code="PLM_API_TEST",
    defect_code="P180101-00001",
    reason="Root cause: Incorrect bounds checking in rendering code",
    countermeasure="Applied fix in commit a1b2c3d, included in v1.3.0"
)
```

## API Methods

### Retrieval Methods

#### `get_defect_info()`
Get detailed information about one or more defects.

```python
response = client.get_defect_info(
    division_code="25",  # 25=Mobile, 26=Network
    defect_codes=["P190404-00007"],  # Up to 99 codes
    system_code="PLM_API_TEST"  # Optional
)
```

**Response Fields**:
- `defectId`: PLM system ID
- `defectCode`: Case code (e.g., P190404-00007)
- `plmTitle`: Defect title
- `plmStatus`: Open, Resolve, Close
- `plmPriority`: A, B, C
- `mainOwnerId`, `mainOwnerName`: Owner info
- `content`, `reason`, `countermeasure`: Details
- `createUser`, `createDate`: Creation info
- `swResolveVersion`: Version where fixed

#### `get_defect_list()`
Get all defects assigned to a specific owner.

```python
response = client.get_defect_list(
    division_code="25",
    main_owner_id="your_knox_id"
)
```

#### `get_defect_history()`
Get change history for defects.

```python
response = client.get_defect_history(
    division_code="25",
    defect_codes=["P190404-00007"]
)
```

#### `get_file_list()`
List all files attached to a defect.

```python
response = client.get_file_list(
    division_code="25",
    defect_code="P190404-00007"
)
```

#### `get_defect_code_list()`
Get available defect codes.

```python
response = client.get_defect_code_list()
```

### Creation/Modification Methods

#### `register_defect()`
Register a new defect (Draft or Open).

```python
request = DefectRegistrationRequest(
    divisionCode="25",
    systemCode="YOUR_SYSTEM",
    changeType="DRAFT",  # or "OPEN"
    refObjectName="Project Name",
    refObjectType="MFG",  # PRE, BASIC, DEV, SW, SUPPORT, ITEM, MFG, ETC, SWREL
    externalDefectId="YOUR_ID_123",
    defectCategory="SW",  # HW, SW, MW
    createUser="knox_id",
    title="Issue Title",
    inChargeUser="user1,user2",  # First is main owner
    Content="Problem description",
    importance="B",  # A, B, C
    occurRateType="Sometimes",  # Always, Sometimes, Once
    occurPhase="DV",  # Development phase
    testUnit="Test Unit Name",
    testItem="Test Item Name",
    functionBlock="Function Block",
    detailFunctionclass="Feature Name",
)

response = client.register_defect(request)
```

**Mandatory Fields** (varies by division):
- divisionCode, systemCode, changeType
- refObjectName, refObjectType
- externalDefectId
- defectCategory, createUser, title
- inChargeUser, Content
- importance, occurRateType, occurPhase
- testUnit, testItem, functionBlock, detailFunctionclass

#### `modify_defect()`
Modify an existing defect.

```python
request = DefectModifyRequest(
    divisionCode="25",
    systemCode="YOUR_SYSTEM",
    changeType="MODIFY",  # or "MODIFY_TRACKING_INFO"
    createUser="knox_id",
    defectId="00EIYX38PtPMWL1000",
    defectCode="P190404-00007",
    importance="A",  # Update any field as needed
    title="Updated Title",
)

response = client.modify_defect(request)
```

#### `register_comment()`
Add, modify, or delete a comment.

```python
# Add new comment
request = CommentRegistrationRequest(
    divisionCode="25",
    systemCode="YOUR_SYSTEM",
    defectCode="P190404-00007",
    defectComment="Comment text",
    createUser="knox_id",
    changeType="S"  # S=Save, M=Modify, D=Delete
)

# Modify existing comment
request.defectCommentId = "01YJK98RTtPMWL1000"
request.changeType = "M"

# Delete comment
request.changeType = "D"

response = client.register_comment(request)
```

### Status Transition Methods

#### `resolve_defect()`
Provide a solution for a defect (must be in Open status).

```python
response = client.resolve_defect(
    division_code="25",
    system_code="YOUR_SYSTEM",
    defect_code="P180101-00001",
    reason="Root cause analysis...",
    countermeasure="Solution/fix...",
    change_type="S"  # S=New, A=Append, T=Temporary
)
```

#### `reject_resolution()`
Reject an attempted solution (defect must be in Resolve status).

```python
response = client.reject_resolution(
    division_code="25",
    system_code="YOUR_SYSTEM",
    defect_code="P180101-00001",
    reject_type="원인/분석 불충분",  # Rejection reason
    reject_comment="Detailed reason why solution doesn't work",
    reject_user="reviewer_knox_id"
)
```

**Reject Types**:
- `문제점 해결 안됨`: Problem Not Resolved
- `원인/분석 불충분`: Insufficient Reason/Analysis
- `해결버전 오입력`: Wrong input of Resolution Version
- `현상태 유지 불가`: Unavailable to Maintain Current Status
- `Side Effect`: Side Effect
- `기타`: ETC

#### `close_defect()`
Close a resolved defect.

```python
response = client.close_defect(
    division_code="25",
    system_code="YOUR_SYSTEM",
    defect_code="P180101-00001"
)
```

#### `draft_to_open()`
Move defect from Draft to Open status.

```python
response = client.draft_to_open(
    division_code="25",
    system_code="TANK",  # or "IAP"
    defect_code="P180101-00001",
    create_user="knox_id",
    external_defect_id="EXT_001",  # Mandatory for TANK
    review_dept="Dept",              # Mandatory for IAP
    test_unit="Test Unit",           # Mandatory for IAP
    model_number="SM-G990B"          # Mandatory for IAP
)
```

#### `cancel_defect()`
Cancel a defect.

```python
response = client.cancel_defect(
    division_code="25",
    system_code="YOUR_SYSTEM",
    defect_code="P180101-00001",
    cancel_comment="Reason for cancellation"
)
```

### Additional Methods

#### `reassign_main_owner()`
Change the main owner of a defect.

```python
response = client.reassign_main_owner(
    division_code="25",
    defect_code="P180101-00001",
    new_owner_id="new_owner_knox_id",
    system_code="YOUR_SYSTEM"
)
```

#### `upload_file()`
Upload a file to a defect (up to 2GB).

```python
response = client.upload_file(
    division_code="25",
    defect_code="P190404-00007",
    file_path="/path/to/file.zip"
)
```

#### `download_file()`
Download a file from a defect.

```python
response = client.download_file(
    file_id="FILE_ID_123",
    division_code="25"
)
```

## Enumerations

### DivisionCode
```python
from plm_api_client import DivisionCode

DivisionCode.MOBILE.value      # "25"
DivisionCode.NETWORK.value     # "26"
DivisionCode.VD.value          # "11"
DivisionCode.LIV.value         # "14"
```

### ChangeType
```python
from plm_api_client import ChangeType

ChangeType.DRAFT.value
ChangeType.OPEN.value
ChangeType.MODIFY.value
ChangeType.DRAFT_TO_OPEN.value
ChangeType.REJECT.value
ChangeType.CLOSE.value
ChangeType.CANCEL.value
ChangeType.SAVE.value           # For comments
ChangeType.MODIFY_COMMENT.value
ChangeType.DELETE_COMMENT.value
```

### DefectCategory
```python
DefectCategory.HW.value   # "HW"
DefectCategory.SW.value   # "SW"
DefectCategory.MW.value   # "MW"
```

### ImportanceLevel
```python
ImportanceLevel.A.value   # "A"
ImportanceLevel.B.value   # "B"
ImportanceLevel.C.value   # "C"
```

### OccurrenceRate
```python
OccurrenceRate.ALWAYS.value      # "Always"
OccurrenceRate.SOMETIMES.value   # "Sometimes"
OccurrenceRate.ONCE.value        # "Once"
```

### Phase
Development phases vary by project type:
```python
Phase.CA, Phase.ER1, Phase.ER2  # Preceding
Phase.BC                        # Basic Comm.
Phase.DV, Phase.PV, Phase.PR, Phase.SR  # Set development
Phase.COMPLETE                  # Support Projects
Phase.DEVELOPMENT               # Maintenance
Phase.SWDEVELOP                 # SW Dev
```

## Response Handling

All API methods return an `APIResponse` object:

```python
response = client.get_defect_info(...)

# Check success
if response.is_success():
    # Access result data
    defects = response.result['defectList']
else:
    # Get error message
    error_msg = response.get_error_message()
    print(f"Error: {error_msg}")

# Response structure
response.status    # Status information (code, message, errorCode)
response.result    # API result data
```

## Error Handling

```python
from plm_api_client import PLMAPIException

try:
    response = client.register_defect(request)
    if response.is_success():
        print("Success")
    else:
        print(f"API Error: {response.get_error_message()}")
except PLMAPIException as e:
    print(f"Request Error: {e}")
except Exception as e:
    print(f"Unexpected Error: {e}")
```

## Common Error Codes

| Code | Description |
|------|-------------|
| PLM_API_00 | Request processed normally (Success) |
| PLM_API_01 | APP ID is not registered |
| DEFECT_API_00 | API call successful (Alternative) |
| DEFECT_API_01 | User does not have 'Quality Viewer' authority |

## Authentication

The API uses Knox ID-based authentication:

1. **singleId**: Your Knox ID (username)
2. **appId**: Registered application ID
3. **userLang**: Language preference (default: 'en')

These are configured in the client initialization:

```python
client = PLMDefectAPIClient(
    base_url="...",
    knox_id="your_knox_id",     # Must be registered
    app_id="YOUR_APP_ID"        # Must be registered with PLM admin
)
```

## Server Information

### Test Server (Staging)
- URL: `http://10.195.55.11:8080/plmapi/broker.do`
- Data: Virtual/test data only
- Note: New users must register account and sign security pledge

### Production Server
- Contact PLM administrator for URL and access

## Important Notes

1. **No Proxy**: Workplace has proxy environments; set `disable_proxy=True` (default)

2. **Mandatory Fields**: Vary by division and operation:
   - Check the `defectRegistrationRequest` dataclass
   - Mobile (25) and Network (26) have different requirements
   - Some fields are conditional (e.g., for IAP vs TANK)

3. **Multiple Defect Codes**: 
   - `get_defect_info()` supports up to 99 defect codes
   - Pass as list: `defect_codes=["P190404-00007", "P191014-00003"]`

4. **File Upload**: Supports up to 2GB files

5. **Comment Editor Mode**: 
   - Set `isCommentEditorYn='Y'` if comment contains tags

6. **Change Type Notes**:
   - For modification: Use `MODIFY_TRACKING_INFO` for vendor tracking only
   - For resolution: Use `A` to append, `T` for temporary save
   - For comments: First user is main owner when registering defects

7. **System Code Usage**:
   - Each external system must have registered systemCode
   - Examples: TANK, IAP, STTS, GPG, BTSP, etc.
   - Provide exact text when calling APIs

## Testing

Use the example file to test functionality:

```bash
python plm_api_example.py
```

Edit `plm_api_example.py` to:
- Set your Knox ID and App ID
- Uncomment example methods to run them
- Modify parameters for your test environment

## Reference

Complete API documentation: **PLM Defect Rest API Guide_20260424.xlsx**

Sheets covered:
- Overview, Error Code
- Defect Info, Defect Registration, Defect Modify
- Draft to Open, Reject, Resolve, Close Defect
- Comment Registration, Defect List, Defect History
- Cancel Defect, Main Owner Reassign
- File List, File Download, File Upload
- Get Sub Folder List, Update Create User
- DefectCode List

## License

Internal use only. Developed for Samsung PLM integration.

## Support

For issues or questions:
1. Verify API guide (Excel) for latest specifications
2. Check error codes in Error Code sheet
3. Contact PLM administration team
