"""Thread pool the routes share for blocking work (password hashing, file writes), so it never runs on the event loop."""
import concurrent.futures

# The case flow's model calls have their own pools (routes/cases.py for transcription,
# case_assessment.py for assessment and extraction).
fast_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="fast")
