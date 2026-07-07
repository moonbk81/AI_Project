"""
PLM (Product Lifecycle Management) API Integration

Comprehensive Python client and integration tools for Samsung PLM Defect REST API.

Main Components:
- plm_api_client: Core API client with 20+ methods
- plm_rag_integration: RAG integration and document management
- plm_dashboard: Streamlit dashboard components
- plm_config.yaml: Configuration management

Quick Start:
    from plm.plm_api_client import PLMDefectAPIClient

    client = PLMDefectAPIClient(
        base_url="http://10.195.55.11:8080/plmapi/broker.do",
        knox_id="your_id",
        app_id="your_app"
    )

    response = client.get_defect_info(
        division_code="25",
        defect_codes=["P190404-00007"]
    )

See INTEGRATION_GUIDE.md for complete documentation.
"""

__version__ = "1.0.0"
__author__ = "AI Project Team"

from .plm_api_client import (
    PLMDefectAPIClient,
    DivisionCode,
    ChangeType,
    DefectCategory,
    ImportanceLevel,
    OccurrenceRate,
    Phase,
    RejectType,
    DefectRegistrationRequest,
    DefectModifyRequest,
    CommentRegistrationRequest,
    APIResponse,
    PLMAPIException,
)

from .plm_rag_integration import (
    PLMConfigManager,
    PLMRAGIntegration,
    PLMDefectContextBuilder,
    PLMDocument,
    create_plm_integration,
)

__all__ = [
    # API Client
    'PLMDefectAPIClient',
    'APIResponse',
    'PLMAPIException',

    # Enums
    'DivisionCode',
    'ChangeType',
    'DefectCategory',
    'ImportanceLevel',
    'OccurrenceRate',
    'Phase',
    'RejectType',

    # Request classes
    'DefectRegistrationRequest',
    'DefectModifyRequest',
    'CommentRegistrationRequest',

    # RAG Integration
    'PLMConfigManager',
    'PLMRAGIntegration',
    'PLMDefectContextBuilder',
    'PLMDocument',
    'create_plm_integration',
]
