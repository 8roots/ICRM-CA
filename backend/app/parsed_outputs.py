from dataclasses import asdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Document,
    DocumentBlock,
    DocumentOutput,
    DocumentPage,
    SealCandidate,
    TableCell,
)
from app.parsing import ParsedOutput


def store_parsed_output(db: Session, document_id: str, parsed: ParsedOutput) -> DocumentOutput:
    db.query(Document).filter_by(id=document_id).with_for_update().one()
    version = (
        db.query(func.coalesce(func.max(DocumentOutput.version), 0))
        .filter_by(document_id=document_id)
        .scalar()
        + 1
    )
    output = DocumentOutput(
        document_id=document_id,
        version=version,
        status=parsed.status,
        parser_version=parsed.parser_version,
        model_version=parsed.model_version,
    )
    for parsed_page in parsed.pages:
        page = DocumentPage(
            number=parsed_page.number,
            width=parsed_page.width,
            height=parsed_page.height,
            status=parsed_page.status,
            error_code=parsed_page.error_code,
        )
        for parsed_block in parsed_page.blocks:
            block = DocumentBlock(
                order=parsed_block.order,
                kind=parsed_block.kind,
                text=parsed_block.text,
                x0=parsed_block.bbox[0] if parsed_block.bbox else None,
                y0=parsed_block.bbox[1] if parsed_block.bbox else None,
                x1=parsed_block.bbox[2] if parsed_block.bbox else None,
                y1=parsed_block.bbox[3] if parsed_block.bbox else None,
                extraction_method=parsed_block.extraction_method,
                confidence=parsed_block.confidence,
                locator=asdict(parsed_block.locator) if parsed_block.locator else None,
            )
            for parsed_cell in parsed_block.cells:
                block.cells.append(
                    TableCell(
                        row_index=parsed_cell.row,
                        column_index=parsed_cell.column,
                        text=parsed_cell.text,
                        x0=parsed_cell.bbox[0] if parsed_cell.bbox else None,
                        y0=parsed_cell.bbox[1] if parsed_cell.bbox else None,
                        x1=parsed_cell.bbox[2] if parsed_cell.bbox else None,
                        y1=parsed_cell.bbox[3] if parsed_cell.bbox else None,
                        locator=asdict(parsed_cell.locator) if parsed_cell.locator else None,
                    )
                )
            page.blocks.append(block)
        for parsed_seal in parsed_page.seals:
            page.seals.append(
                SealCandidate(
                    text=parsed_seal.text,
                    x0=parsed_seal.bbox[0],
                    y0=parsed_seal.bbox[1],
                    x1=parsed_seal.bbox[2],
                    y1=parsed_seal.bbox[3],
                    confidence=parsed_seal.confidence,
                    model_version=parsed.model_version,
                )
            )
        output.pages.append(page)
    db.add(output)
    db.flush()
    return output
