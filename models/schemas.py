from pydantic import BaseModel, Field


class SpreadsheetWriteResult(BaseModel):
    success: bool = Field(description="Whether spreadsheet write operation was succesful or not")
    row_address: str = Field(default="", description="The cell range address of the newly written row")


class JobSpecVerification(BaseModel):
    is_valid: bool = Field(description="If the job field data retrieved by an LLM is accurate to what the job posting says")
    issues: list[str] = Field(default=[], description="Discrepancies found between the extracted job data and the source job post")


class NamedItem(BaseModel):
    id: str = Field(description="The ID of a retrieved digital item") 
    name: str = Field(description="The name of a retrieved digital item")


class WorkbookItem(BaseModel):
    id: str = Field(description="The ID of the retrieved workbook")
    name: str = Field(description="The filename of the retrieved workbook")
    path: str = Field(description="The fully qualified file path to the workbook within the user's drive")
