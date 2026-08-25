"""
PLM Auto-Download & Log Extraction Pipeline

Handles automatic file downloads and log file processing.
- Auto-saves files to the local Downloads folder
- Registers extracted LOG files for the analysis pipeline

Reading the archives themselves lives in `core.log_archive`, which knows
nothing about Streamlit.
"""

import logging
from typing import Optional, Dict, Tuple
from pathlib import Path

import streamlit as st

from core.log_archive import extract_logs_from_zip

logger = logging.getLogger(__name__)


def add_pending_log(filename: str, content: bytes) -> bool:
    """
    Register an extracted log file for the unified analysis pipeline.

    The sidebar pipeline consumes ``st.session_state.plm_pending_logs`` directly,
    so no intermediate queue/status tracking is needed.

    Args:
        filename: Name of the log file
        content: File content as bytes

    Returns:
        True if registered successfully
    """
    try:
        if 'plm_pending_logs' not in st.session_state:
            st.session_state.plm_pending_logs = []

        st.session_state.plm_pending_logs.append({
            'filename': filename,
            'content': content,
        })
        logger.info(f"Registered {filename} for analysis (size: {len(content)} bytes)")
        return True
    except Exception as e:
        logger.error(f"Failed to register pending log: {e}")
        return False


class AutoDownloadManager:
    """
    Manage automatic file downloads

    Handles browser download simulation and file management
    """

    @staticmethod
    def get_downloads_folder() -> Path:
        """
        Get the user's Downloads folder path

        Returns:
            Path to Downloads folder
        """
        home = Path.home()

        # Try common locations
        download_paths = [
            home / "Downloads",           # Linux, macOS
            home / "사용자" / "Downloads",  # Korean Windows
            home / "AppData" / "Downloads",  # Windows
        ]

        for path in download_paths:
            if path.exists():
                return path

        # Fallback to home/Downloads
        return home / "Downloads"

    @staticmethod
    def save_to_downloads(filename: str, content: bytes) -> Tuple[bool, str]:
        """
        Save file to Downloads folder

        Args:
            filename: Name of file to save
            content: File content as bytes

        Returns:
            Tuple (success, message/path)
        """
        try:
            downloads_folder = AutoDownloadManager.get_downloads_folder()
            downloads_folder.mkdir(parents=True, exist_ok=True)

            filepath = downloads_folder / filename

            # Handle duplicate filenames
            counter = 1
            base_name = filename
            name_parts = filename.rsplit('.', 1) if '.' in filename else (filename, '')

            while filepath.exists():
                if name_parts[1]:
                    new_name = f"{name_parts[0]}_{counter}.{name_parts[1]}"
                else:
                    new_name = f"{filename}_{counter}"
                filepath = downloads_folder / new_name
                counter += 1

            # Write file
            with open(filepath, 'wb') as f:
                f.write(content)

            logger.info(f"File saved: {filepath}")
            return True, str(filepath)

        except Exception as e:
            logger.error(f"Failed to save to Downloads: {e}")
            return False, str(e)


class PLMAutoDownloadFlow:
    """
    Orchestrate the complete auto-download → extract → analyze flow
    """

    @staticmethod
    def process_downloaded_file(
        filename: str,
        file_content: bytes,
        source_defect: Optional[str] = None,
        auto_save: bool = True,
        auto_extract_logs: bool = True,
        auto_analyze: bool = True
    ) -> Dict:
        """
        Process a downloaded file through the complete pipeline

        Args:
            filename: Name of downloaded file
            file_content: File content as bytes
            source_defect: Optional defect code source
            auto_save: Whether to auto-save to Downloads folder
            auto_extract_logs: Whether to auto-extract log files from ZIP
            auto_analyze: Whether to auto-start analysis pipeline after extraction

        Returns:
            Dictionary with processing results
        """
        result = {
            'filename': filename,
            'success': False,
            'saved_path': None,
            'is_zip': False,
            'extracted_logs': [],
            'messages': []
        }

        try:
            # Check if ZIP file
            is_zip = filename.lower().endswith('.zip')
            result['is_zip'] = is_zip

            if auto_save and not is_zip:
                # For non-ZIP files, just save directly
                success, path_or_error = AutoDownloadManager.save_to_downloads(filename, file_content)
                result['saved_path'] = path_or_error
                result['success'] = success
                result['messages'].append(f"File saved to: {path_or_error}")
                return result

            # Process ZIP file
            if is_zip and auto_extract_logs:
                # Extract all logs from ZIP
                logs = extract_logs_from_zip(file_content, return_all=False)

                if logs:
                    result['extracted_logs'] = list(logs.keys())
                    result['messages'].append(f"Found {len(logs)} log file(s)")

                    # Register logs directly for the unified analysis pipeline
                    for log_filename, log_content in logs.items():
                        success = add_pending_log(log_filename, log_content)
                        if success:
                            result['messages'].append(f"✅ {log_filename} 분석 파이프라인에 추가됨")
                        else:
                            result['messages'].append(f"❌ {log_filename} 추가 실패")

                    result['success'] = True
                else:
                    result['messages'].append("⚠️ No log files found in ZIP")

                    # Still save the ZIP for reference
                    if auto_save:
                        success, path_or_error = AutoDownloadManager.save_to_downloads(filename, file_content)
                        result['saved_path'] = path_or_error
                        result['messages'].append(f"ZIP saved to: {path_or_error}")
            else:
                # Non-ZIP or no auto-extract
                if auto_save:
                    success, path_or_error = AutoDownloadManager.save_to_downloads(filename, file_content)
                    result['saved_path'] = path_or_error
                    result['success'] = success
                    result['messages'].append(f"File saved to: {path_or_error}")

        except Exception as e:
            logger.error(f"Error processing file: {e}", exc_info=True)
            result['messages'].append(f"Error: {str(e)}")

        # Extraction only queues the logs. Starting the analysis/DB ingest is left to
        # the user's explicit "분석 및 DB 적재 시작" click in the sidebar, so that
        # picking a defect never blocks on a long-running analysis.
        if result['success'] and result['extracted_logs']:
            result['messages'].append("📥 분석 대기열에 추가됨 - Sidebar에서 분석을 시작하세요")

        return result
