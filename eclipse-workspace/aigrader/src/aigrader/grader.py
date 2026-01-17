class AIGrader:
    """
    Top-level orchestrator for AI-based grading.

    Responsibilities (to be implemented incrementally):
    - Validate assignment preconditions (rubric, submission)
    - Extract submission content
    - Evaluate submission using an LLM
    - Write rubric-based assessment back to Canvas
    """

    def __init__(self, *, canvas_client=None, llm_client=None, config=None):
        """
        Dependencies are injected to keep this class testable.

        Parameters
        ----------
        canvas_client : object
            Abstraction over Canvas API access.
        llm_client : object
            Abstraction over LLM API access.
        config : object
            Configuration object (timeouts, model name, thresholds, etc.).
        """
        self.canvas_client = canvas_client
        self.llm_client = llm_client
        self.config = config

    def grade_assignment(
        self,
        course_id: int,
        assignment_id: int,
        user_id: int | None = None,
    ):
        """
        Grade a single assignment (optionally for a single student).

        Parameters
        ----------
        course_id : int
            Canvas course ID.
        assignment_id : int
            Canvas assignment ID.
        user_id : int | None
            If provided, grade only this student's submission.
            If None, behavior will later be defined (e.g., first submission or batch).

        Raises
        ------
        NotImplementedError
            Until grading pipeline is implemented.
        """
        raise NotImplementedError("Grading pipeline not yet implemented.")
