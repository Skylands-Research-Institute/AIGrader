"""
Batch processing for grading multiple assignments.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class AssignmentSpec:
    """Specification for an assignment to grade."""
    course_id: int
    assignment_id: int
    enabled: bool = True
    model: Optional[str] = None
    notes: str = ""


@dataclass
class BatchResult:
    """Results from batch grading operation."""
    total: int
    succeeded: int
    failed: int
    failures: List[Tuple[int, int, str]]  # (course_id, assignment_id, error_msg)


def _truthy(v: Any) -> bool:
    """Convert various values to boolean."""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in {"1", "true", "t", "yes", "y", "on"}


def load_assignment_file(path: str) -> List[AssignmentSpec]:
    """
    Load assignment specifications from a TSV/CSV file.
    
    Expected header columns (case-insensitive):
      - course_id (required)
      - assignment_id (required)
      - enabled (optional, default: true)
      - model (optional)
      - notes (optional)
    
    Args:
        path: Path to TSV or CSV file
        
    Returns:
        List of AssignmentSpec objects
        
    Raises:
        ValueError: If file format is invalid or required fields are missing
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)

        # Auto-detect delimiter: tab or comma
        delimiter = "\t" if "\t" in sample.splitlines()[0] else ","
        reader = csv.DictReader(f, delimiter=delimiter)

        specs: List[AssignmentSpec] = []
        for i, row in enumerate(reader, start=2):  # header is line 1
            if not row:
                continue
            
            # Normalize keys to lowercase
            row_norm = {
                str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) 
                for k, v in row.items()
            }

            try:
                course_id = int(row_norm.get("course_id") or 0)
                assignment_id = int(row_norm.get("assignment_id") or 0)
            except Exception as e:
                raise ValueError(
                    f"Invalid course_id/assignment_id at line {i}: {row}"
                ) from e

            if course_id <= 0 or assignment_id <= 0:
                raise ValueError(
                    f"Missing/invalid course_id or assignment_id at line {i}: {row}"
                )

            enabled = _truthy(row_norm.get("enabled", "true"))
            model = (row_norm.get("model") or "").strip() or None
            notes = (row_norm.get("notes") or "").strip()

            specs.append(
                AssignmentSpec(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    enabled=enabled,
                    model=model,
                    notes=notes,
                )
            )
        
        return specs


class BatchGrader:
    """
    Handles batch grading of multiple assignments.
    """
    
    def __init__(self, verbose: bool = True):
        """
        Initialize batch grader.
        
        Args:
            verbose: Whether to print progress information
        """
        self.verbose = verbose
    
    def process_assignment_file(
        self,
        file_path: str,
        grade_callback: Callable[[AssignmentSpec], int],
        *,
        skip_disabled: bool = True,
    ) -> BatchResult:
        """
        Process all assignments from a file.
        
        Args:
            file_path: Path to assignment file (TSV or CSV)
            grade_callback: Function to call for each assignment.
                           Should accept AssignmentSpec and return 0 on success.
            skip_disabled: Whether to skip assignments marked as disabled
            
        Returns:
            BatchResult with statistics and failures
        """
        specs = load_assignment_file(file_path)
        
        if skip_disabled:
            specs = [s for s in specs if s.enabled]
        
        return self.process_assignments(specs, grade_callback)
    
    def process_assignments(
        self,
        assignments: List[AssignmentSpec],
        grade_callback: Callable[[AssignmentSpec], int],
    ) -> BatchResult:
        """
        Process a list of assignment specifications.
        
        Args:
            assignments: List of AssignmentSpec to process
            grade_callback: Function to call for each assignment
            
        Returns:
            BatchResult with statistics and failures
        """
        total = len(assignments)
        succeeded = 0
        failures: List[Tuple[int, int, str]] = []
        
        for idx, spec in enumerate(assignments, start=1):
            banner = (
                f"[{idx}/{total}] course_id={spec.course_id} "
                f"assignment_id={spec.assignment_id}"
            )
            if spec.model:
                banner += f" model={spec.model}"
            if spec.notes:
                banner += f" notes={spec.notes}"
            
            if self.verbose:
                print("\n" + "=" * len(banner))
                print(banner)
                print("=" * len(banner))
            
            try:
                rc = grade_callback(spec)
                if rc != 0:
                    failures.append(
                        (spec.course_id, spec.assignment_id, "nonzero return")
                    )
                else:
                    succeeded += 1
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                failures.append((spec.course_id, spec.assignment_id, error_msg))
                if self.verbose:
                    print(
                        f"ERROR grading course_id={spec.course_id} "
                        f"assignment_id={spec.assignment_id}: {e}"
                    )
        
        return BatchResult(
            total=total,
            succeeded=succeeded,
            failed=len(failures),
            failures=failures,
        )
