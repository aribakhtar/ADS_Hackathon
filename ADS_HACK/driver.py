import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from large_doc_extractor.param_pipeline import run_param_pipeline
from processor.pdf_processor import PDFProcessor


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SUBMISSION = PROJECT_ROOT / "submissions.csv"
DEFAULT_RAW_PDF_DIR = PROJECT_ROOT / "data" / "raw_pdfs"
DEFAULT_MARKDOWN_DIR = PROJECT_ROOT / "data" / "extracted_pdfs_mds"
DEFAULT_LARGE_DOC_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "large_doc_extractor"


class SubmissionDriver:
    def __init__(
        self,
        submission_path: Path = DEFAULT_SUBMISSION,
        raw_pdf_dir: Path = DEFAULT_RAW_PDF_DIR,
        markdown_dir: Path = DEFAULT_MARKDOWN_DIR,
        large_doc_output_dir: Path = DEFAULT_LARGE_DOC_OUTPUT_DIR,
        output_path: Optional[Path] = None,
        indication: str = "Psoriasis",
        reuse_markdown: bool = True,
        use_llm_vocabulary: bool = True,
        limit: Optional[int] = None,
    ):
        self.submission_path = submission_path
        self.raw_pdf_dir = raw_pdf_dir
        self.markdown_dir = markdown_dir
        self.large_doc_output_dir = large_doc_output_dir
        self.output_path = output_path or submission_path
        self.indication = indication
        self.reuse_markdown = reuse_markdown
        self.use_llm_vocabulary = use_llm_vocabulary
        self.limit = limit

        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        self.large_doc_output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Path:
        logger.info("Reading submissions from %s", self.submission_path)
        df = pd.read_csv(self.submission_path, dtype=str).fillna("")
        column_lookup = {column.strip(): column for column in df.columns}
        filename_column = column_lookup["Filename"]
        brand_column = column_lookup["Brand"]

        processed = 0
        for _, row in df.iterrows():
            if self.limit is not None and processed >= self.limit:
                break

            filename = str(row[filename_column]).strip()
            brand = str(row[brand_column]).strip()
            if not filename or not brand:
                continue

            logger.info("Processing %s | %s", filename, brand)
            result = self.process_submission(filename, brand)
            source_csv = self.output_path if self.output_path.exists() else self.submission_path
            self.output_filler(result, source_csv, self.output_path)

            processed += 1

        logger.info("Completed %s submission row(s). Output: %s", processed, self.output_path)
        return self.output_path

    def process_submission(self, filename: str, brand: str) -> Dict[str, Any]:
        pdf_path = self._resolve_pdf_path(filename)
        markdown_path = self.pdf_to_markdown(pdf_path)
        extraction_result = self.extract_json(markdown_path, filename, brand)
        access_result = self.calculate_access_score(filename, brand)

        brand_attributes = _extract_brand_attributes(access_result)
        if not brand_attributes:
            brand_attributes = _extract_brand_attributes(extraction_result)

        return {
            "file_name": filename,
            "brand": brand,
            "brand_attributes": brand_attributes,
            "large_doc_result": extraction_result,
            "access_score_result": access_result,
        }

    def pdf_to_markdown(self, pdf_path: Path) -> Path:
        PDFProcessor.validate_pdf(pdf_path)
        markdown_path = self.markdown_dir / f"{pdf_path.stem}.md"

        if self.reuse_markdown and markdown_path.exists():
            logger.info("Using existing markdown: %s", markdown_path)
            return markdown_path

        logger.info("Extracting markdown from PDF: %s", pdf_path)
        markdown_text = PDFProcessor(str(pdf_path)).extract_text()
        markdown_path.write_text(markdown_text, encoding="utf-8")
        logger.info("Saved markdown: %s", markdown_path)
        return markdown_path

    def extract_json(self, markdown_path: Path, filename: str, brand: str) -> Dict[str, Any]:
        return run_param_pipeline(
            markdown_file=str(markdown_path),
            brand=brand,
            filename=filename,
            indication=self.indication,
            output_dir=str(self.large_doc_output_dir),
            use_llm_vocabulary=self.use_llm_vocabulary,
            verbose=True,
        )

    def calculate_access_score(self, filename: str, brand: str) -> Dict[str, Any]:
        from access_score_finder import access_scorer

        access_scorer.LARGE_DOC_OUTPUT_DIR = self.large_doc_output_dir
        return access_scorer.calculate_access_score(filename, brand)

    def output_filler(self, result: Dict[str, Any], submission: Path, output_path: Optional[Path] = None) -> Path:
        logger.info("Filling output with extracted attributes and access score.")
        df = pd.read_csv(submission, dtype=str).fillna("")

        column_lookup = {column.strip(): column for column in df.columns}
        field_to_header = {
            "filename": "Filename",
            "brand": "Brand",
            "age": "Age",
            "step_therapy_requirements": "Step Therapy Requirements Documented in Policy",
            "number_of_steps_brands": "Number of Steps through Brands",
            "number_of_steps_generic": "Number of Steps through Generic",
            "step_through_phototherapy": "Step through-Phototherapy",
            "tb_test_required": "TB Test required",
            "quantity_limits": "Quantity Limits",
            "specialist_types": "Specialist Types",
            "initial_auth_duration": "Initial Authorization Duration(in-months)",
            "reauthorization_duration": "Reauthorization Duration(in-months)",
            "reauthorization_required": "Reauthorization Required",
            "reauthorization_requirements": "Reauthorization Requirements Documented in Policy",
            "access_score": "Access Score",
        }

        brand_attributes = _extract_brand_attributes(result)
        filename = str(brand_attributes.get("filename") or result.get("file_name") or "").strip()
        brand = str(brand_attributes.get("brand") or result.get("brand") or "").strip()

        filename_column = column_lookup["Filename"]
        brand_column = column_lookup["Brand"]
        row_mask = (
            df[filename_column].astype(str).str.strip().str.lower().eq(filename.lower())
            & df[brand_column].astype(str).str.strip().str.lower().eq(brand.lower())
        )

        if not row_mask.any():
            df.loc[len(df)] = {column: "" for column in df.columns}
            row_mask = df.index == df.index[-1]

        for field_name, header in field_to_header.items():
            column = column_lookup.get(header)
            if column is None:
                logger.warning("Skipping missing output column: %s", header)
                continue

            if field_name == "filename":
                value = filename
            elif field_name == "brand":
                value = brand
            else:
                value = brand_attributes.get(field_name, "")
            df.loc[row_mask, column] = "" if value is None else str(value)

        output_path = output_path or submission
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        logger.info("Saved filled output CSV to %s", output_path)
        return output_path

    def _resolve_pdf_path(self, filename: str) -> Path:
        candidate = Path(filename)
        if candidate.is_absolute() and candidate.exists():
            return candidate

        pdf_path = self.raw_pdf_dir / candidate.name
        if pdf_path.exists():
            return pdf_path

        matches = [path for path in self.raw_pdf_dir.glob("*.pdf") if path.name.lower() == candidate.name.lower()]
        if matches:
            return matches[0]

        raise FileNotFoundError(f"Could not find PDF for {filename} in {self.raw_pdf_dir}")


def _extract_brand_attributes(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}

    brand_attributes = result.get("brand_attributes")
    if isinstance(brand_attributes, dict):
        return brand_attributes
    if isinstance(brand_attributes, list) and brand_attributes:
        return brand_attributes[0]

    access_score_result = result.get("access_score_result")
    if isinstance(access_score_result, dict):
        nested = _extract_brand_attributes_from_nested_result(access_score_result)
        if nested:
            return nested

    large_doc_result = result.get("large_doc_result")
    if isinstance(large_doc_result, dict):
        nested = _extract_brand_attributes(large_doc_result)
        if nested:
            return nested

    return _extract_brand_attributes_from_nested_result(result)


def _extract_brand_attributes_from_nested_result(result: Dict[str, Any]) -> Dict[str, Any]:
    for value in result.values():
        if not isinstance(value, dict):
            continue
        brand_attributes = value.get("brand_attributes")
        if isinstance(brand_attributes, dict):
            return brand_attributes
        if isinstance(brand_attributes, list) and brand_attributes:
            return brand_attributes[0]
    return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ADS submission extraction pipeline.")
    parser.add_argument("--submission", default=str(DEFAULT_SUBMISSION), help="Input submissions CSV.")
    parser.add_argument("--output", help="Output CSV path. Defaults to updating the submission CSV in place.")
    parser.add_argument("--raw-pdf-dir", default=str(DEFAULT_RAW_PDF_DIR), help="Directory containing input PDFs.")
    parser.add_argument("--markdown-dir", default=str(DEFAULT_MARKDOWN_DIR), help="Directory for extracted markdown.")
    parser.add_argument(
        "--large-doc-output-dir",
        default=str(DEFAULT_LARGE_DOC_OUTPUT_DIR),
        help="Directory for large_doc_extractor JSON outputs.",
    )
    parser.add_argument("--indication", default="Plaque psoriasis (PsO)", help="Target indication.")
    parser.add_argument(
        "--refresh-markdown",
        action="store_true",
        help="Re-extract markdown even when a matching .md file already exists.",
    )
    parser.add_argument(
        "--no-llm-vocabulary",
        action="store_true",
        help="Use deterministic vocabulary scan only before parameter extraction.",
    )
    parser.add_argument("--limit", type=int, help="Process only the first N non-empty submission rows.")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> Path:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    driver = SubmissionDriver(
        submission_path=Path(args.submission),
        raw_pdf_dir=Path(args.raw_pdf_dir),
        markdown_dir=Path(args.markdown_dir),
        large_doc_output_dir=Path(args.large_doc_output_dir),
        output_path=Path(args.output) if args.output else None,
        indication=args.indication,
        reuse_markdown=not args.refresh_markdown,
        use_llm_vocabulary=not args.no_llm_vocabulary,
        limit=args.limit,
    )
    return driver.run()


if __name__ == "__main__":
    main()
