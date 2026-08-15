from pydantic import BaseModel, Field


class SpreadsheetWriteResult(BaseModel):
    success: bool = Field(description="Whether spreadsheet write operation was succesful or not")
    row_address: str = Field(
        default="", description="The cell range address of the newly written row"
    )


class JobSpecVerification(BaseModel):
    is_valid: bool = Field(
        description="If the extracted job field data is accurate to what the job posting says"
    )
    issues: list[str] = Field(
        default=[],
        description="Discrepancies found between the extracted job data and the source job post",
    )


class AccessCheck(BaseModel):
    access_blocked: bool = Field(
        description=(
            "True if the extracted fields indicate the posting page was blocked/inaccessible "
            "(e.g. a login wall, CAPTCHA, or restricted-access message) rather than genuinely "
            "extracted job content"
        )
    )
    reason: str = Field(
        default="",
        description=(
            "A clear summary, no longer than 2 lines, of what looks like it happened and why -- "
            "only populated when access_blocked is true"
        ),
    )


class NamedItem(BaseModel):
    id: str = Field(description="The ID of a retrieved digital item")
    name: str = Field(description="The name of a retrieved digital item")


class FolderItem(BaseModel):
    id: str = Field(description="The ID of the retrieved item")
    name: str = Field(description="The name of the retrieved item")
    is_folder: bool = Field(
        description="True if this item is a folder, False if it's a workbook file"
    )
    path: str = Field(
        default="",
        description=(
            "The fully qualified path of the item within the drive; only "
            "populated for workbook files, since folder paths are tracked "
            "separately via current_folder_path"
        ),
    )
