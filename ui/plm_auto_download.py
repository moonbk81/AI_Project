"""
PLM Auto-Download & Log Extraction Pipeline

Handles automatic file downloads, ZIP extraction, and log file processing.
- Auto-saves files to browser's Downloads folder
- Extracts LOG files from ZIP archives
- Integrates with analysis pipeline for automatic processing
"""

import os
import re
import zipfile
import io
import logging
from typing import Optional, Dict, List, Tuple
from pathlib import Path

import streamlit as st

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


class LogFileExtractor:
    """Extract log files from various archive formats"""

    # Log file patterns to match
    LOG_PATTERNS = [
        r'^dumpstate\.log$',              # dumpstate.log (case insensitive match)
        r'^dumpstate\.txt$',              # dumpstate.txt
        r'^dumpState\.log$',              # dumpState.log
        r'^dumpState_\d+\.log$',          # dumpState_1783577655961.log (Unix timestamp only)
        r'^dumpState_[A-Z0-9]+_\d{10,}\.log$',  # dumpState_S911NKSS7EZCI_202607070957.log (device ID + timestamp)
        r'^act_dumpstate\.txt$',          # act_dumpstate.txt
    ]

    @staticmethod
    def is_log_file(filename: str) -> bool:
        """
        Check if filename matches any log file pattern

        Args:
            filename: File name to check

        Returns:
            True if matches log pattern, False otherwise
        """
        for pattern in LogFileExtractor.LOG_PATTERNS:
            if re.match(pattern, filename, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def extract_logs_from_zip(zip_data: bytes, return_all: bool = False) -> Dict[str, bytes]:
        """
        Extract log files from ZIP archive

        Args:
            zip_data: Binary data of ZIP file
            return_all: If True, return all files; if False, only return log files

        Returns:
            Dictionary {filename: file_content} for matching files
        """
        extracted = {}

        try:
            zip_buffer = io.BytesIO(zip_data)

            with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    # Skip directories
                    if file_info.is_dir():
                        continue

                    filename = file_info.filename

                    # For subdirectories, use only the filename
                    base_filename = os.path.basename(filename)

                    # Check if it's a log file (only check filename, not full path)
                    if return_all or LogFileExtractor.is_log_file(base_filename):
                        try:
                            file_content = zip_ref.read(filename)
                            extracted[base_filename] = file_content
                        except Exception as e:
                            logger.error(f"Failed to extract {filename}: {e}")

            return extracted

        except zipfile.BadZipFile:
            logger.error("Invalid ZIP file")
            return {}
        except Exception as e:
            logger.error(f"Error extracting from ZIP: {e}")
            return {}

    @staticmethod
    def extract_single_log(zip_data: bytes, target_filename: str) -> Optional[bytes]:
        """
        Extract a single file from ZIP

        Args:
            zip_data: Binary data of ZIP file
            target_filename: Target file name (base filename, not full path)

        Returns:
            File content as bytes, or None if not found
        """
        try:
            zip_buffer = io.BytesIO(zip_data)

            with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
                # Search for the file (handle nested directories)
                for file_info in zip_ref.infolist():
                    if os.path.basename(file_info.filename) == target_filename:
                        return zip_ref.read(file_info.filename)

                # Direct match as fallback
                if target_filename in zip_ref.namelist():
                    return zip_ref.read(target_filename)

                return None

        except Exception as e:
            logger.error(f"Error extracting single file: {e}")
            return None


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
                logs = LogFileExtractor.extract_logs_from_zip(file_content, return_all=False)

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

        # Flag auto-start of the analysis pipeline if logs were extracted
        if auto_analyze and result['success'] and result['extracted_logs']:
            st.session_state.trigger_auto_analysis = True
            result['messages'].append("🚀 자동 분석 파이프라인 시작 중...")

        return result
