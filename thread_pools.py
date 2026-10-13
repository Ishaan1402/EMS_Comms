"""Thread pools the routes share for blocking work, so it never runs on the event loop."""
import concurrent.futures

# Separate pools: fast ops (auth, file writes) vs slow external calls (the older recording flow's
# OpenAI calls, SMS and email). The case flow's model calls have their own pools
# (routes/cases.py for transcription, case_assessment.py for assessment and extraction).
fast_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="fast")
slow_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="slow")
