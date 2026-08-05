from pydantic import BaseModel


class SpreadsheetWriteResult(BaseModel):
    success: bool
    row_address: str = ""


class JobSpecVerification(BaseModel):
    is_valid: bool
    issues: list[str] = []


class NamedItem(BaseModel):
    id: str
    name: str


class WorkbookItem(BaseModel):
    id: str
    name: str
    path: str
