
from aigrader import AIGrader
from aigrader.canvas import CanvasClient, CanvasAuth

client = CanvasClient(CanvasAuth(base_url="https://sussexccc.instructure.com", 
                                 token="10233~XTF8E8uGZLfFhkf282Qe6Y8FBKEBhMN8FQTMJUMeTaHCwFxA9LkBVMB6JZABBVwy"))
grader = AIGrader(canvas_client=client)
print(grader.grade_assignment(16388, 364682, 28700))