-- 계획 실패의 종류. input: 같은 입력으로는 다시 해도 실패(예산 부족 등), operator: 운영자 조치가 필요(AWS 로그인 만료 등),
-- transient: 일시 오류(다시 시도). 화면이 종류에 맞는 안내를 보여 준다.
ALTER TABLE plan_failures
    ADD COLUMN kind ENUM('input', 'operator', 'transient') NOT NULL DEFAULT 'transient' AFTER reason;
