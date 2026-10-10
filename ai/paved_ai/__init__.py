"""Paved Clouds AI: 코드 분석.

업로드한 소스에서 배포에 필요한 값(포트·헬스체크·DB·환경 변수·초기화 명령·Dockerfile)을 찾아 분석 결과로 남긴다.
구성 단계·비용·계획은 인프라 worker(infra/worker)가 이 결과를 읽어 만든다.

HTTP·DB를 모르는 순수 로직만 둔다. 백엔드 작업이 끝나면 이 패키지를 back/app/llm/ 으로 옮긴다.
"""
