
from aigrader import AIGrader
from aigrader.config import AIGraderConfig

grader = AIGrader(config=AIGraderConfig())
print(grader)
grader.grade_assignment(16388, 364682, 28700)
