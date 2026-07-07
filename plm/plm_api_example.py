"""
PLM Defect API Client - Usage Examples

This module demonstrates how to use the PLM Defect API Client
"""

from plm_api_client import (
    PLMDefectAPIClient,
    DefectRegistrationRequest,
    DefectModifyRequest,
    CommentRegistrationRequest,
    DivisionCode,
    ChangeType,
    DefectCategory,
    ImportanceLevel,
    OccurrenceRate,
    Phase,
    RejectType,
    PLMAPIException
)


def example_init_client():
    """Example: Initialize API Client"""
    client = PLMDefectAPIClient(
        base_url="http://10.195.55.11:8080/plmapi/broker.do",
        knox_id="your_knox_id",
        app_id="PLM_API_TEST",
        user_lang="en"
    )
    return client


def example_get_defect_info(client: PLMDefectAPIClient):
    """Example: Get defect information"""
    try:
        response = client.get_defect_info(
            division_code=DivisionCode.MOBILE.value,
            defect_codes=["P190404-00007", "P191014-00003"]
        )

        if response.is_success():
            print("✓ Defect info retrieved successfully")
            defect_list = response.result.get('defectList', [])
            for defect in defect_list:
                print(f"  - {defect.get('defectCode')}: {defect.get('plmTitle')}")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_register_defect(client: PLMDefectAPIClient):
    """Example: Register a new defect"""
    try:
        request = DefectRegistrationRequest(
            divisionCode=DivisionCode.MOBILE.value,
            systemCode="PLM_API_TEST",
            changeType=ChangeType.DRAFT.value,
            refObjectName="Galaxy S24",  # Project/Model name
            refObjectType="MFG",  # Manufacturing Model
            externalDefectId="EXT_DEF_001",  # External system ID
            defectCategory=DefectCategory.SW.value,
            createUser="your_knox_id",
            title="Sample defect registration test",
            inChargeUser="user1,user2",  # First user is main owner
            Content="Description of the defect problem",
            importance=ImportanceLevel.B.value,
            occurRateType=OccurrenceRate.SOMETIMES.value,
            occurPhase=Phase.DV.value,
            testUnit="S/W Engineering",
            testItem="Functional Test",
            functionBlock="Display",
            detailFunctionclass="Screen Rendering",
            reappearancePath="Steps to reproduce",
            forecastResult="Expected correct behavior",
            swVersion="S24.1.0",
            hwVersion="REV1.0"
        )

        response = client.register_defect(request)

        if response.is_success():
            defect_id = response.result.get('defectId')
            defect_code = response.result.get('defectCode')
            print(f"✓ Defect registered successfully")
            print(f"  - Defect ID: {defect_id}")
            print(f"  - Defect Code: {defect_code}")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_modify_defect(client: PLMDefectAPIClient):
    """Example: Modify an existing defect"""
    try:
        request = DefectModifyRequest(
            divisionCode=DivisionCode.MOBILE.value,
            systemCode="PLM_API_TEST",
            changeType=ChangeType.MODIFY.value,
            createUser="your_knox_id",
            defectId="00EIYX38PtPMWL1000",
            defectCode="P190404-00007",
            importance=ImportanceLevel.A.value,
            title="Updated defect title",
            Content="Updated problem description"
        )

        response = client.modify_defect(request)

        if response.is_success():
            print("✓ Defect modified successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_resolve_defect(client: PLMDefectAPIClient):
    """Example: Provide solution for a defect"""
    try:
        response = client.resolve_defect(
            division_code=DivisionCode.MOBILE.value,
            system_code="PLM_API_TEST",
            defect_code="P180101-00001",
            reason="Root cause analysis shows that...",
            countermeasure="The fix was applied in commit XYZ...",
            change_type=ChangeType.SAVE.value
        )

        if response.is_success():
            print("✓ Defect resolved successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_reject_resolution(client: PLMDefectAPIClient):
    """Example: Reject a defect resolution"""
    try:
        response = client.reject_resolution(
            division_code=DivisionCode.MOBILE.value,
            system_code="PLM_API_TEST",
            defect_code="P180101-00001",
            reject_type=RejectType.PROBLEM_NOT_RESOLVED.value,
            reject_comment="The fix doesn't completely solve the issue",
            reject_user="reviewer_knox_id"
        )

        if response.is_success():
            print("✓ Resolution rejected successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_register_comment(client: PLMDefectAPIClient):
    """Example: Register a comment on a defect"""
    try:
        request = CommentRegistrationRequest(
            divisionCode=DivisionCode.MOBILE.value,
            systemCode="PLM_API_TEST",
            defectCode="P190404-00007",
            defectComment="This issue is also occurring in the latest build",
            createUser="your_knox_id",
            changeType=ChangeType.SAVE.value
        )

        response = client.register_comment(request)

        if response.is_success():
            comment_id = response.result.get('defectCommentId')
            print(f"✓ Comment registered successfully")
            print(f"  - Comment ID: {comment_id}")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_modify_comment(client: PLMDefectAPIClient):
    """Example: Modify an existing comment"""
    try:
        request = CommentRegistrationRequest(
            divisionCode=DivisionCode.MOBILE.value,
            systemCode="PLM_API_TEST",
            defectCode="P190404-00007",
            defectCommentId="01YJK98RTtPMWL1000",
            defectComment="Updated comment text with additional details",
            createUser="your_knox_id",
            changeType=ChangeType.MODIFY_COMMENT.value
        )

        response = client.register_comment(request)

        if response.is_success():
            print("✓ Comment modified successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_close_defect(client: PLMDefectAPIClient):
    """Example: Close a defect"""
    try:
        response = client.close_defect(
            division_code=DivisionCode.MOBILE.value,
            system_code="PLM_API_TEST",
            defect_code="P180101-00001"
        )

        if response.is_success():
            print("✓ Defect closed successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_draft_to_open(client: PLMDefectAPIClient):
    """Example: Move defect from Draft to Open"""
    try:
        response = client.draft_to_open(
            division_code=DivisionCode.MOBILE.value,
            system_code="TANK",
            defect_code="P180101-00001",
            create_user="your_knox_id",
            external_defect_id="EXT_DEF_001"
        )

        if response.is_success():
            print("✓ Defect moved to Open successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_cancel_defect(client: PLMDefectAPIClient):
    """Example: Cancel a defect"""
    try:
        response = client.cancel_defect(
            division_code=DivisionCode.MOBILE.value,
            system_code="PLM_API_TEST",
            defect_code="P180101-00001",
            cancel_comment="Cancelled due to duplicate entry"
        )

        if response.is_success():
            print("✓ Defect cancelled successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_get_defect_list(client: PLMDefectAPIClient):
    """Example: Get defect list by main owner"""
    try:
        response = client.get_defect_list(
            division_code=DivisionCode.MOBILE.value,
            main_owner_id="your_knox_id"
        )

        if response.is_success():
            defect_list = response.result.get('defectList', [])
            print(f"✓ Found {len(defect_list)} defects")
            for defect in defect_list[:5]:  # Show first 5
                print(f"  - {defect.get('defectCode')}: {defect.get('plmTitle')}")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_get_defect_history(client: PLMDefectAPIClient):
    """Example: Get defect history"""
    try:
        response = client.get_defect_history(
            division_code=DivisionCode.MOBILE.value,
            defect_codes=["P190404-00007"]
        )

        if response.is_success():
            history = response.result.get('history', [])
            print(f"✓ Found {len(history)} history entries")
            for entry in history[:5]:
                print(f"  - {entry.get('action')}: {entry.get('timestamp')}")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_reassign_main_owner(client: PLMDefectAPIClient):
    """Example: Reassign main owner"""
    try:
        response = client.reassign_main_owner(
            division_code=DivisionCode.MOBILE.value,
            defect_code="P180101-00001",
            new_owner_id="new_owner_knox_id",
            system_code="PLM_API_TEST"
        )

        if response.is_success():
            print("✓ Main owner reassigned successfully")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


def example_get_file_list(client: PLMDefectAPIClient):
    """Example: Get file list from defect"""
    try:
        response = client.get_file_list(
            division_code=DivisionCode.MOBILE.value,
            defect_code="P190404-00007"
        )

        if response.is_success():
            files = response.result.get('fileList', [])
            print(f"✓ Found {len(files)} files")
            for file in files:
                print(f"  - {file.get('fileName')} ({file.get('fileSize')} bytes)")
        else:
            print(f"✗ Error: {response.get_error_message()}")

    except PLMAPIException as e:
        print(f"✗ API Error: {e}")


if __name__ == "__main__":
    # Initialize client
    client = example_init_client()

    print("PLM Defect API Client - Examples\n")
    print("=" * 50)

    # Run examples
    print("\n1. Get Defect Info")
    print("-" * 50)
    example_get_defect_info(client)

    print("\n2. Register Defect")
    print("-" * 50)
    # example_register_defect(client)  # Uncomment to run

    print("\n3. Modify Defect")
    print("-" * 50)
    # example_modify_defect(client)  # Uncomment to run

    print("\n4. Resolve Defect")
    print("-" * 50)
    # example_resolve_defect(client)  # Uncomment to run

    print("\n5. Register Comment")
    print("-" * 50)
    # example_register_comment(client)  # Uncomment to run

    print("\n6. Get Defect List")
    print("-" * 50)
    # example_get_defect_list(client)  # Uncomment to run

    print("\n" + "=" * 50)
    print("See examples in this file for more use cases")
