from dataclasses import dataclass


@dataclass(frozen=True)
class Defect:
    defect_id: str
    defect_code: str
    title: str
    status: str
    reason: str
