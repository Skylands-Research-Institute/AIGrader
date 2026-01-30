"""
Formatting utilities for prompts and comments.
"""


def format_assignment_description_section(description: str) -> str:
    """
    Format an assignment description for inclusion in the grading prompt.
    
    This creates a clearly-delimited section that provides context about
    the assignment to the LLM, while making clear that the rubric takes
    precedence if there's any conflict.
    
    Args:
        description: Raw assignment description (may be HTML)
        
    Returns:
        Formatted section text for prompt inclusion
    """
    desc = (description or "").strip()
    if not desc:
        return (
            "=== ASSIGNMENT DESCRIPTION (Student-Facing Context) ===\n"
            "Intent:\n"
            "This section would reproduce the assignment description shown to students.\n"
            "No assignment description was available for this assignment.\n"
            "=== END ASSIGNMENT DESCRIPTION ==="
        )

    return (
        "=== ASSIGNMENT DESCRIPTION (Student-Facing Context) ===\n"
        "Intent:\n"
        "This section reproduces the assignment description shown to students.\n"
        "It is provided to help interpret student intent and task context.\n\n"
        "Scope:\n"
        "Contextual understanding only.\n\n"
        "Limitations:\n"
        "This section does not override the rubric or introduce additional grading criteria.\n"
        "If there is any conflict, the rubric governs.\n\n"
        f"{desc}\n"
        "=== END ASSIGNMENT DESCRIPTION ==="
    )
