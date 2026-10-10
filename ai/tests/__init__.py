import os

# 테스트는 가짜 LLM 답을 쓰므로 실제 캐시 폴더에 남기거나 다른 테스트의 답을 꺼내 쓰지 않게 끔.
# 캐시 동작은 tests/test_llm.py CacheTests 가 임시 폴더로 따로 확인함
os.environ["PAVED_AI_CACHE"] = "off"
