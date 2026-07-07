"""
PLM Defect API - RAG Integration Module

This module integrates PLM defect information with the RAG (Retrieval-Augmented Generation)
system to enrich AI analysis with resolution data and historical context.
"""

import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict
import yaml
import os

from .plm_api_client import (
    PLMDefectAPIClient,
    DivisionCode,
    APIResponse,
    PLMAPIException
)


logger = logging.getLogger(__name__)


@dataclass
class PLMDocument:
    """Document structure for RAG ingestion"""
    doc_id: str  # Unique document ID
    title: str
    content: str
    metadata: Dict[str, Any]
    source: str = "plm"
    created_at: str = None
    updated_at: str = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return asdict(self)


class PLMConfigManager:
    """Manage PLM configuration"""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration manager

        Args:
            config_path: Path to plm_config.yaml (default: ./plm/plm_config.yaml)
        """
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__),
                'plm_config.yaml'
            )

        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded PLM config from {self.config_path}")
            return config
        except FileNotFoundError:
            logger.error(f"Config file not found: {self.config_path}")
            return {}
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports nested keys with dots)"""
        keys = key.split('.')
        value = self.config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default

        return value if value is not None else default

    def resolve_env_vars(self, value: Any) -> Any:
        """
        Resolve environment variables in config values

        Priority:
        1. If value is not a template (no ${...}), use it as-is
        2. If value is a template ${VAR_NAME}, check if VAR_NAME is set in environment
        3. If not found in environment, return the original template string
        """
        if isinstance(value, str):
            # Check if it's an environment variable template like ${VAR_NAME}
            if value.startswith("${") and value.endswith("}"):
                env_var = value[2:-1]
                # Try to get from environment, return template if not found
                env_value = os.getenv(env_var)
                if env_value:
                    logger.info(f"Using {env_var} from environment variable")
                    return env_value
                else:
                    # Environment variable not set, keep the template for error message
                    return value
        return value

    def get_plm_client(self) -> PLMDefectAPIClient:
        """Create PLM API client from configuration"""
        base_url = self.resolve_env_vars(self.get('plm.production_url'))
        if not base_url:
            base_url = self.resolve_env_vars(self.get('plm.base_url'))
        knox_id = self.resolve_env_vars(self.get('plm.knox_id'))
        app_id = self.resolve_env_vars(self.get('plm.app_id'))
        user_lang = self.get('plm.user_lang', 'en')
        disable_proxy = self.get('plm.disable_proxy', True)

        # Provide helpful error messages
        if not base_url:
            raise ValueError("PLM base_url or production_url not configured in plm_config.yaml")
        if not knox_id or knox_id.startswith("${"):
            raise ValueError(
                "PLM knox_id not configured. \n"
                "Option 1: Set in plm_config.yaml: knox_id: 'your_knox_id'\n"
                "Option 2: Set environment variable: export PLM_KNOX_ID='your_knox_id'"
            )
        if not app_id or app_id.startswith("${"):
            raise ValueError(
                "PLM app_id not configured. \n"
                "Option 1: Set in plm_config.yaml: app_id: 'your_app_id'\n"
                "Option 2: Set environment variable: export PLM_APP_ID='your_app_id'"
            )

        logger.info(f"Creating PLM client for: {base_url}")
        return PLMDefectAPIClient(
            base_url=base_url,
            knox_id=knox_id,
            app_id=app_id,
            user_lang=user_lang,
            disable_proxy=disable_proxy
        )


class PLMRAGIntegration:
    """Integrate PLM defect data with RAG system"""

    def __init__(self, config_manager: Optional[PLMConfigManager] = None):
        """
        Initialize PLM-RAG integration

        Args:
            config_manager: PLMConfigManager instance (creates default if None)
        """
        self.config = config_manager or PLMConfigManager()
        self.client = self.config.get_plm_client()
        self.documents: List[PLMDocument] = []
        self._setup_logging()

    def _setup_logging(self):
        """Setup logging based on config"""
        log_level = self.config.get('logging.level', 'INFO')
        log_file = self.config.get('logging.file', 'logs/plm_api.log')

        # Create logs directory if needed
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        handler = logging.FileHandler(log_file)
        handler.setLevel(getattr(logging, log_level))
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    def fetch_defect_info(
        self,
        division_code: str,
        defect_codes: List[str]
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch defect information from PLM

        Args:
            division_code: Division code (25=Mobile, 26=Network)
            defect_codes: List of defect codes to fetch

        Returns:
            Defect information dict or None on failure
        """
        try:
            response = self.client.get_defect_info(
                division_code=division_code,
                defect_codes=defect_codes
            )

            if response.is_success():
                return response.result
            else:
                logger.error(f"Failed to fetch defect info: {response.get_error_message()}")
                return None

        except PLMAPIException as e:
            logger.error(f"PLM API error: {e}")
            return None

    def defect_to_rag_document(
        self,
        defect: Dict[str, Any]
    ) -> PLMDocument:
        """
        Convert PLM defect to RAG document

        Args:
            defect: Defect information dictionary

        Returns:
            PLMDocument ready for RAG ingestion
        """
        defect_code = defect.get('defectCode', 'UNKNOWN')
        title = defect.get('plmTitle', '')

        # Build content from multiple fields
        content_parts = []

        # Problem description
        if defect.get('content'):
            content_parts.append(f"**Problem:**\n{defect['content']}")

        # Root cause analysis
        if defect.get('reason'):
            content_parts.append(f"**Root Cause:**\n{defect['reason']}")

        # Solution/Countermeasure
        if defect.get('countermeasure'):
            content_parts.append(f"**Solution:**\n{defect['countermeasure']}")

        # Reappearance path
        if defect.get('reappearancePath'):
            content_parts.append(f"**Steps to Reproduce:**\n{defect['reappearancePath']}")

        # Expected result
        if defect.get('forecastResult'):
            content_parts.append(f"**Expected Result:**\n{defect['forecastResult']}")

        content = "\n\n".join(content_parts)

        # Build metadata
        metadata = {
            'defect_code': defect_code,
            'status': defect.get('plmStatus'),
            'priority': defect.get('plmPriority'),
            'main_owner': defect.get('mainOwnerName'),
            'owner_id': defect.get('mainOwnerId'),
            'created_by': defect.get('createUser'),
            'version_detected': defect.get('swRegVersion'),
            'version_resolved': defect.get('swResolveVersion'),
            'test_unit': defect.get('testUnit'),
            'function_block': defect.get('functionBlock'),
        }

        return PLMDocument(
            doc_id=defect_code,
            title=title,
            content=content,
            metadata=metadata,
            created_at=defect.get('createDate'),
            updated_at=defect.get('updateDate')
        )

    def fetch_and_convert_defects(
        self,
        division_code: str,
        defect_codes: List[str]
    ) -> List[PLMDocument]:
        """
        Fetch defects and convert to RAG documents

        Args:
            division_code: Division code
            defect_codes: List of defect codes

        Returns:
            List of PLMDocument objects
        """
        defect_info = self.fetch_defect_info(division_code, defect_codes)

        if not defect_info:
            return []

        documents = []
        for defect in defect_info.get('defectList', []):
            try:
                doc = self.defect_to_rag_document(defect)
                documents.append(doc)
                logger.info(f"Converted defect {doc.doc_id} to RAG document")
            except Exception as e:
                logger.error(f"Failed to convert defect: {e}")
                continue

        self.documents.extend(documents)
        return documents

    def get_documents_for_rag(self) -> List[Dict[str, Any]]:
        """
        Get all documents in RAG-compatible format

        Returns:
            List of document dictionaries
        """
        return [doc.to_dict() for doc in self.documents]

    def clear_documents(self):
        """Clear cached documents"""
        self.documents = []
        logger.info("Cleared cached PLM documents")


class PLMDefectContextBuilder:
    """Build context for AI analysis from PLM data"""

    def __init__(self, integration: PLMRAGIntegration):
        """
        Initialize context builder

        Args:
            integration: PLMRAGIntegration instance
        """
        self.integration = integration

    def build_defect_context(
        self,
        defect_code: str,
        division_code: str = "25"
    ) -> Optional[Dict[str, Any]]:
        """
        Build comprehensive context for a single defect

        Args:
            defect_code: Defect case code
            division_code: Division code

        Returns:
            Context dictionary with defect information
        """
        defect_info = self.integration.fetch_defect_info(division_code, [defect_code])

        if not defect_info or not defect_info.get('defectList'):
            return None

        defect = defect_info['defectList'][0]

        return {
            'defect_code': defect.get('defectCode'),
            'title': defect.get('plmTitle'),
            'status': defect.get('plmStatus'),
            'priority': defect.get('plmPriority'),
            'problem': defect.get('content'),
            'root_cause': defect.get('reason'),
            'solution': defect.get('countermeasure'),
            'main_owner': defect.get('mainOwnerName'),
            'created_date': defect.get('createDate'),
            'updated_date': defect.get('updateDate'),
            'version_detected': defect.get('swRegVersion'),
            'version_resolved': defect.get('swResolveVersion'),
        }

    def build_batch_context(
        self,
        defect_codes: List[str],
        division_code: str = "25"
    ) -> Dict[str, Any]:
        """
        Build context for multiple defects

        Args:
            defect_codes: List of defect codes
            division_code: Division code

        Returns:
            Batch context with summary and individual defects
        """
        self.integration.fetch_and_convert_defects(division_code, defect_codes)
        documents = self.integration.get_documents_for_rag()

        # Build summary statistics
        statuses = {}
        priorities = {}
        for doc in documents:
            status = doc['metadata'].get('status')
            priority = doc['metadata'].get('priority')

            if status:
                statuses[status] = statuses.get(status, 0) + 1
            if priority:
                priorities[priority] = priorities.get(priority, 0) + 1

        return {
            'total_defects': len(documents),
            'statuses': statuses,
            'priorities': priorities,
            'documents': documents,
            'summary': {
                'total_count': len(documents),
                'status_distribution': statuses,
                'priority_distribution': priorities,
            }
        }


def create_plm_integration(config_path: Optional[str] = None) -> PLMRAGIntegration:
    """
    Factory function to create PLM-RAG integration

    Args:
        config_path: Optional path to config file

    Returns:
        PLMRAGIntegration instance
    """
    config = PLMConfigManager(config_path)
    return PLMRAGIntegration(config)


if __name__ == "__main__":
    # Example usage
    logging.basicConfig(level=logging.INFO)

    # Initialize integration
    integration = create_plm_integration()

    # Example: Fetch defect information
    print("Fetching defect information...")
    # defect_codes = ["P190404-00007"]
    # documents = integration.fetch_and_convert_defects("25", defect_codes)
    #
    # for doc in documents:
    #     print(f"\nDefect: {doc.title}")
    #     print(f"Content: {doc.content[:100]}...")
    #     print(f"Metadata: {doc.metadata}")

    print("PLM-RAG Integration ready")
