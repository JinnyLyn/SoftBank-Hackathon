# 운영자 AWS 계정 준비·이전 경계

이 문서는 [관리형 배포 방향](PRODUCT_DIRECTION.md)에 따른 **팀 운영자용** 안내다. 사용자 가입·배포 화면에서 따라 하게 하는 절차가 아니다. 10/10 회의록의 새 계정 연결 메모를 제품 온보딩과 분리한다. 문서 반영은 역할 생성·권한 확대·AWS apply·DNS 변경·기존 자원 삭제 승인이 아니다.

## 역할과 프로필

- 기존 운영 계정에서 새 workload 계정을 사용하려면 대상 역할의 trust policy와 호출 주체의 `sts:AssumeRole` 권한을 함께 확인한다. 교육 계정의 권한 제한은 우회하지 않는다.
- 연결별 ExternalId와 허용 principal을 관리한다. 실제 계정 ID·사용자 PC 경로·프로필 이름은 운영 설정에서 확인하며 제품 기본값이나 Git 문서에 고정하지 않는다.
- 회의 메모의 `AdministratorAccess`는 제품의 필수 요구사항이 아니다. 기존 배포 역할 권한을 확인하고 필요한 범위를 정한다. 역할 신뢰 정책·권한 확대는 운영자 실행 범위에서 별도로 처리한다.
- 역할 최대 세션 기간을 4시간으로 설정했다고 모든 호출이 4시간 동안 자동 갱신되는 것은 아니다. 역할 체이닝은 최대 1시간이고, 갱신은 CLI/SDK의 credential provider와 원본 자격 증명의 유효성에 의존한다. 이미 발급한 STS 값을 환경 변수에 고정하면 자동 갱신된다고 가정하지 않는다.
- 역할 프로필은 키 자체를 적지 않지만 `source_profile` 등 자격 증명 출처는 필요하다. 프로필만 추가하면 인증이 생기는 것은 아니다. 읽기 전용 확인 예시는 `aws sts get-caller-identity --profile <운영자프로필>`이다.

## 계정 전환 전 확인

1. 실행할 CLI/SDK/Terraform 각각의 실제 account·role·region을 확인한다. `AWS_PROFILE` 상속만으로 대상 계정이 보장되지 않는다. 정적 환경 자격 증명 등 다른 credential 출처와 provider 설정도 확인한다.
2. 소스/대상 계정의 bootstrap·foundation·앱 state 소유자, S3 backend·잠금·배포 폴더를 확인한다. 기존 state를 보존하고 계정별로 구분한다. AWS에는 자원이 있는데 로컬 state가 비어 있는 상태에서 재생성하지 않는다.
3. ECR 이미지, SSM ARN, RDS, ALB, 인증서, foundation 출력과 저장 plan은 계정에 종속된다. 기존 plan을 새 계정에 적용하지 않는다. 대상 계정 기준으로 검증·계획·승인을 새로 수행한다.
4. `allowed_account_ids` 같은 Terraform 방어 장치는 구현 여부를 코드에서 확인한 후 사용한다. 문서에만 있는 변수를 이미 적용된 기능으로 안내하지 않는다.
5. 선택한 구성에 필요한 Fargate vCPU·ALB·RDS·Elastic IP 등을 실제 사용량과 함께 확인한다. NAT 3대가 쓰는 EIP와 여유분을 계산하며, “항상 EIP 5개 필요”처럼 임의 수치를 필수값으로 만들지 않는다.

실제 변경은 운영자가 확인한 계획에 따라 bootstrap → foundation → 시험 앱 순서로 진행한다. 각 계정의 state·출력·자원 식별자를 검증하고, 기존 환경 정리는 새 환경 접속 확인과 전환 승인 이후에 별도로 한다. 실행 명령과 기존 구조는 [infra/README](../infra/README.md)를 따른다.

## DNS와 인증서가 다른 계정에 있을 때

- ALB에 연결할 ACM 인증서는 ALB와 같은 계정·리전에 준비한다. DNS hosted zone은 다른 계정에 있을 수 있다.
- DNS를 변경하는 주체는 해당 zone의 제한된 권한을 사용한다. 인증서 발급 계정과 DNS 관리 계정을 혼동하지 않는다.
- 사용자 독립 도메인도 같은 방식으로 연결할 수 있다. 팀 소유 시험 도메인은 사용자 최종 주소나 소유권 모델을 강제하는 근거가 아니다.
- 기존 DNS 레코드·메일 설정·인증서·공유 ALB 규칙을 통째로 정리하지 않는다. 변경 대상·소유권·기존 참조·되돌릴 값을 확인하고 승인된 부분만 전환한다.
- 앱 삭제는 도메인 등록 해지나 갱신 중단을 의미하지 않는다. 공유 인증서/zone 삭제와 사용자 도메인 등록 해지는 별도 수명 주기다.

## 공식 근거

- [교차 계정 역할](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html)
- [역할 세션과 체이닝 제한](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-cli.html)
- [CLI 역할 프로필과 credential cache](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-role.html)
- [ALB 인증서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/https-listener-certificates.html)
- [다른 계정의 ALB로 DNS 연결](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-to-elb-load-balancer.html)
