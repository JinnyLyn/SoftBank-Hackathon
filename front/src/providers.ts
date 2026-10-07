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
    summary: 'IAM 역할 위임',
    howto: [
      'IAM → 역할 만들기 → "다른 AWS 계정" 선택',
      '외부 ID에 {externalId} 입력, 정책은 AdministratorAccess 대신 안내된 최소 권한 정책 사용',
      '만든 역할의 ARN을 아래에 붙여 넣기',
    ],
    fields: [
      { key: 'roleArn', label: 'IAM Role ARN', placeholder: 'arn:aws:iam::123456789012:role/paved-clouds', wide: true },
      {
        key: 'region',
        label: '리전',
        options: [
          { value: 'ap-northeast-2', label: 'ap-northeast-2 (서울)' },
          { value: 'ap-northeast-1', label: 'ap-northeast-1 (도쿄)' },
          { value: 'us-east-1', label: 'us-east-1 (버지니아)' },
        ],
      },
      budget,
    ],
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
      {
        key: 'region',
        label: '리전',
        options: [
          { value: 'asia-northeast3', label: 'asia-northeast3 (서울)' },
          { value: 'asia-northeast1', label: 'asia-northeast1 (도쿄)' },
          { value: 'us-central1', label: 'us-central1 (아이오와)' },
        ],
      },
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
      {
        key: 'region',
        label: '리전',
        options: [
          { value: 'koreacentral', label: 'koreacentral (한국 중부)' },
          { value: 'japaneast', label: 'japaneast (일본 동부)' },
          { value: 'eastus', label: 'eastus (미국 동부)' },
        ],
      },
      budget,
    ],
  },
  onprem: {
    label: '온프레미스',
    mark: 'SRV',
    kind: 'server',
    summary: 'SSH + Docker',
    howto: [
      '서버에 Docker와 docker compose 설치',
      '배포용 사용자의 ~/.ssh/authorized_keys 에 아래 공개 키 추가\n{publicKey}',
      '접속 정보 입력. 서버 사양은 연결할 때 자동으로 읽습니다.',
    ],
    fields: [
      { key: 'host', label: '호스트', placeholder: '192.168.0.24 또는 server.example.com' },
      { key: 'port', label: 'SSH 포트', placeholder: '22' },
      { key: 'user', label: '사용자', placeholder: 'deploy' },
      { key: 'path', label: '배포 경로', placeholder: '/srv/apps' },
    ],
  },
}

export const PROVIDER_ORDER: Provider[] = ['aws', 'gcp', 'azure', 'onprem']

export const EXTERNAL_ID = 'pc-7f3a91'
export const PUBLIC_KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3v2pYq8m deploy@pavedclouds'
