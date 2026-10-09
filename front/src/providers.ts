import type { Provider } from './types'

export interface FieldDef {
  key: string
  label: string
  placeholder?: string
  options?: { value: string; label: string }[]
  hint?: string
  /** 한 줄 전체 차지 */
  wide?: boolean
  optional?: boolean
}

export interface ProviderMeta {
  label: string
  /** 목록에 붙는 짧은 표시 */
  mark: string
  kind: 'cloud' | 'server'
  summary: string
  /** 연결 방법 안내. {externalId}, {publicKey} 는 화면에서 채움 */
  howto: string[]
  fields: FieldDef[]
}

const budget: FieldDef = {
  key: 'budget',
  label: '월 예산 한도 (USD)',
  placeholder: '30',
  hint: '추천 구성이 한도를 넘으면 승인 전에 알려 드립니다.',
  optional: true,
}

export const PROVIDERS: Record<Provider, ProviderMeta> = {
  aws: {
    label: 'AWS',
    mark: 'AWS',
    kind: 'cloud',
    summary: '콘솔에서 연결 스택 생성',
    howto: [
      '저장하면 AWS 콘솔의 CloudFormation 빠른 생성 화면 링크가 나옵니다',
      '그 화면에서 "스택 생성"만 누르면 Paved Clouds용 역할이 외부 ID {externalId} 로 만들어집니다',
      '스택 생성이 끝나면 "다시 확인"을 눌러 연결을 마칩니다. 계정 ID는 자동으로 읽습니다.',
    ],
    fields: [budget],
  },
  gcp: {
    label: 'Google Cloud',
    mark: 'GCP',
    kind: 'cloud',
    summary: 'Workload Identity 연동',
    howto: [
      'IAM → 서비스 계정 만들기, 역할: Cloud Run 관리자, Cloud SQL 관리자, Compute 관리자',
      'Workload Identity 풀에 공급자 추가, 대상(audience)은 {externalId}',
      '프로젝트 ID와 서비스 계정 이메일 입력',
    ],
    fields: [
      { key: 'projectId', label: '프로젝트 ID', placeholder: 'my-project-1234' },
      { key: 'serviceAccount', label: '서비스 계정 이메일', placeholder: 'paved@my-project-1234.iam.gserviceaccount.com', wide: true },
      budget,
    ],
  },
  azure: {
    label: 'Azure',
    mark: 'AZ',
    kind: 'cloud',
    summary: '페더레이션 자격 증명',
    howto: [
      'Entra ID → 앱 등록 → 페더레이션 자격 증명 추가, 주체 식별자는 {externalId}',
      '구독의 액세스 제어(IAM)에서 이 앱에 기여자 역할 할당',
      '구독, 테넌트, 클라이언트 ID 입력',
    ],
    fields: [
      { key: 'subscriptionId', label: '구독 ID', placeholder: '00000000-0000-0000-0000-000000000000', wide: true },
      { key: 'tenantId', label: '테넌트 ID', placeholder: '00000000-…' },
      { key: 'clientId', label: '클라이언트 ID', placeholder: '00000000-…' },
      budget,
    ],
  },
  onprem: {
    label: '온프레미스',
    mark: 'SRV',
    kind: 'server',
    summary: '설치 명령 한 줄',
    // 온프레미스는 입력 칸 대신 설치 명령 화면(InstallCommand)을 씀
    howto: [],
    fields: [],
  },
}

export const PROVIDER_ORDER: Provider[] = ['aws', 'gcp', 'azure', 'onprem']

export const EXTERNAL_ID = 'pc-7f3a91'

/** 설치 스크립트 미리 보기. 실제 스크립트는 백엔드가 같은 내용으로 제공 */
export const INSTALL_SCRIPT_PREVIEW = `#!/bin/sh
# Paved Clouds 서버 연결 스크립트. root 권한으로 한 번만 실행
# 사용: curl -fsSL https://pavedclouds.dev/install.sh | sudo sh -s -- --token <일회용 토큰>
set -eu
[ "\${1:-}" = "--token" ] && TOKEN="\${2:-}"
[ -n "\${TOKEN:-}" ] || { echo "--token 값이 필요합니다" >&2; exit 1; }

# 1. Docker 설치 (이미 있으면 건너뜀)
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh

# 2. 배포 전용 사용자. docker 그룹은 root에 준하는 권한이라 이 사용자로만 접속함
id deploy >/dev/null 2>&1 || useradd -m -s /bin/sh deploy
usermod -aG docker deploy

# 3. Paved Clouds 공개 키 등록 (이 키로만 접속, 비밀번호 로그인 없음)
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3v2pYq8m deploy@pavedclouds" \\
  >> /home/deploy/.ssh/authorized_keys

# 4. 서버 사양 보고 → 화면이 '연결됨'으로 바뀜
curl -fsS -X POST https://api.pavedclouds.dev/v1/servers/register \\
  -H "Authorization: Bearer $TOKEN" \\
  -d "cpus=$(nproc)&memMb=$(free -m | awk '/Mem/{print $2}')&host=$(hostname -I | awk '{print $1}')"`
