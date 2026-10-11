-- 사용자가 비교표에서 고른 구성 단계. 있으면 worker가 추천 안 대신 이 단계로 계획한다(예산·할당량 안에서 만들 수 있을 때만).
ALTER TABLE projects
    ADD COLUMN requested_tier ENUM('lean', 'balanced', 'roomy') NULL AFTER purpose;
