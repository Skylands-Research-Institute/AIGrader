class AIGraderConfig:
    """
    Configuration for AIGrader behavior.

    This intentionally excludes secrets (API keys).
    """

    def __init__(
        self,
        *,
        model_name: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        min_word_count: int = 150,
        require_rubric: bool = True,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.min_word_count = min_word_count
        self.require_rubric = require_rubric
