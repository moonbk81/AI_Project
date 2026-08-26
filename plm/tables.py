"""Row builders for the PLM tables.

The same defect and attachment dictionaries are shown in several places, each
of which used to cut titles, trim dates and format sizes on its own. Those
rules live here instead, so the tables agree with each other.

Called from the FastAPI routes; nothing here imports a web framework.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Detail page of one defect on the internal PLM site.
DEFECT_DETAIL_URL = "http://splm.sec.samsung.net/wl/tqm/defect/defectreg/goDefectDetail.do"

# Titles are one line in a six-column table; past this they push the other
# columns out of view.
DEFECT_TITLE_CHARS = 80

_MISSING = "N/A"


def defect_site_url(defect_id: str) -> str:
    """Link to a defect on the PLM site."""
    return f"{DEFECT_DETAIL_URL}?isPopUp=Y&menuGubun=&defectId={defect_id}"


def truncate(text: Any, limit: int) -> Any:
    if isinstance(text, str) and len(text) > limit:
        return text[:limit] + "..."
    return text


def date_only(value: Any) -> Any:
    """PLM timestamps are "YYYY-MM-DD HH:MM:SS"; the tables only show the day."""
    if isinstance(value, str) and value:
        return value[:10]
    return value


def format_kb(size: Any) -> str:
    """A known byte count as kilobytes."""
    return f"{(size or 0) / 1024:.1f} KB"


def format_optional_kb(size: Any) -> str:
    """Attachment size, or `"N/A"` when PLM reported none."""
    return format_kb(size) if size else _MISSING


def _defect_link(defect: Dict[str, Any]) -> str:
    """Link cell for `st.column_config.LinkColumn`.

    The column shows the trailing `#<code>` fragment as its text, so the code
    stays readable while the cell still carries the full URL.
    """
    defect_id = defect.get("defectId", "")
    defect_code = defect.get("defectCode", "")
    if not defect_id:
        return ""
    url = defect_site_url(defect_id)
    return f"{url}#{defect_code}" if defect_code else url


def build_defect_rows(
    defects: Optional[List[Dict[str, Any]]],
    *,
    title_chars: int = DEFECT_TITLE_CHARS,
) -> List[Dict[str, Any]]:
    """Search / quick-search results, one row per defect."""
    return [
        {
            "Code": _defect_link(defect),
            "Title": truncate(defect.get("plmTitle", ""), title_chars),
            "Status": defect.get("plmStatus", _MISSING),
            "Priority": defect.get("plmPriority", _MISSING),
            "Owner": defect.get("mainOwnerName", _MISSING),
            "Created": date_only(defect.get("createDate", "")),
        }
        for defect in (defects or [])
    ]


def build_attachment_rows(
    files: Optional[List[Dict[str, Any]]],
    *,
    name_column: str = "Filename",
    include_id: bool = False,
) -> List[Dict[str, Any]]:
    """Attached files, one row per attachment.

    `name_column` and `include_id` cover the two places this is shown: the
    read-only list next to a defect, and the download list that needs the id.
    """
    rows = []
    for attachment in files or []:
        row = {
            name_column: attachment.get("title", _MISSING),
            "Size": format_optional_kb(attachment.get("fileSize", 0)),
            "Created": date_only(attachment.get("createDate", "")),
        }
        if include_id:
            row["ID"] = attachment.get("fileId")
        rows.append(row)
    return rows


def build_archive_rows(contents: Optional[Dict[str, int]]) -> List[Dict[str, Any]]:
    """Files found inside an opened ZIP attachment."""
    return [
        {"File": filename, "Size": format_kb(size)}
        for filename, size in (contents or {}).items()
    ]
