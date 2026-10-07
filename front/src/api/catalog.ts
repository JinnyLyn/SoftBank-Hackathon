// mock 전용 데이터: 대상 종류별 구성안, 생성 코드, 배포 로그
// 실제로는 백엔드(AI)가 분석 결과를 보고 만들어 줌
import type { Provider, Tier, TierKey } from '../types'

type Spec = Tier['resources'][number] & { addr: string }
type CatalogTier = Omit<Tier, 'resources'> & { resources: Spec[] }

const BASE: Record<TierKey, Pick<Tier, 'key' | 'label' | 'fit'>> = {
  lean: { key: 'lean', label: '작게 시작', fit: '시연, MVP, 하루 수십 명' },
  balanced: { key: 'balanced', label: '권장', fit: '동아리, 초기 서비스, 하루 수백 명' },
  roomy: { key: 'roomy', label: '여유 있게', fit: '본격 운영, 하루 수천 명 이상' },
}

export const CATALOG: Record<Provider, CatalogTier[]> = {
  aws: [
    {
      ...BASE.lean,
      headline: '가상 서버 한 대 (EC2)',
      tradeoff: '서버가 멈추면 복구될 때까지 서비스도 멈춥니다',
      resources: [
        { addr: 'aws_instance.app', service: 'EC2', spec: 't4g.small', monthlyUsd: 15.18, why: '동시 접속 수십 명이면 2 vCPU, 2GB로 충분합니다.' },
        { addr: 'aws_ebs_volume.data', service: 'EBS', spec: 'gp3 20GB', monthlyUsd: 1.82, why: 'SQLite 파일을 서버 디스크에 그대로 둡니다.' },
        { addr: 'aws_eip.app', service: 'Elastic IP', spec: '고정 IP 1개', monthlyUsd: 3.65, why: '서버를 껐다 켜도 주소가 바뀌지 않게 합니다.' },
      ],
    },
    {
      ...BASE.balanced,
      headline: '컨테이너 + 관리형 DB',
      tradeoff: '로드밸런서 고정비가 붙습니다',
      resources: [
        { addr: 'aws_ecs_service.app', service: 'ECS Fargate', spec: '0.25 vCPU, 0.5GB × 1', monthlyUsd: 10.37, why: '요청이 몰리면 2개까지 늘립니다.' },
        { addr: 'aws_db_instance.main', service: 'RDS PostgreSQL', spec: 'db.t4g.micro, 20GB', monthlyUsd: 20.88, why: 'SQLite 대신 씁니다. 재시작해도 데이터가 남습니다.' },
        { addr: 'aws_lb.app', service: 'ALB', spec: 'HTTPS 443', monthlyUsd: 18.4, why: 'HTTPS 인증서와 헬스체크를 맡습니다.' },
        { addr: 'aws_cloudwatch_log_group.app', service: 'CloudWatch Logs', spec: '7일 보관', monthlyUsd: 0.8, why: '배포 실패 시 AI가 원인을 볼 로그입니다.' },
      ],
    },
    {
      ...BASE.roomy,
      headline: '이중화 + 자동 확장',
      tradeoff: '지금 규모에서는 사양이 많이 남습니다',
      resources: [
        { addr: 'aws_ecs_service.app', service: 'ECS Fargate', spec: '0.5 vCPU, 1GB × 2~6', monthlyUsd: 41.48, why: 'CPU 60%를 넘으면 작업을 늘립니다.' },
        { addr: 'aws_db_instance.main', service: 'RDS PostgreSQL', spec: 'db.t4g.small, Multi-AZ', monthlyUsd: 78.84, why: '한 쪽 가용 영역이 멈춰도 DB가 살아 있습니다.' },
        { addr: 'aws_lb.app', service: 'ALB', spec: 'HTTPS 443', monthlyUsd: 22.1, why: 'HTTPS와 트래픽 분산을 맡습니다.' },
        { addr: 'aws_cloudwatch_metric_alarm.cpu', service: 'CloudWatch', spec: '로그 30일, 경보 3개', monthlyUsd: 3.2, why: 'CPU, 5xx, DB 연결 수 경보를 겁니다.' },
      ],
    },
  ],
  gcp: [
    {
      ...BASE.lean,
      headline: '가상 서버 한 대 (Compute Engine)',
      tradeoff: '서버가 멈추면 복구될 때까지 서비스도 멈춥니다',
      resources: [
        { addr: 'google_compute_instance.app', service: 'Compute Engine', spec: 'e2-small', monthlyUsd: 15.86, why: '2 vCPU(공유), 2GB로 시연 규모를 감당합니다.' },
        { addr: 'google_compute_disk.data', service: 'Persistent Disk', spec: 'pd-balanced 20GB', monthlyUsd: 2.6, why: 'SQLite 파일을 디스크에 둡니다.' },
        { addr: 'google_compute_address.app', service: '고정 외부 IP', spec: '1개', monthlyUsd: 3.65, why: '재시작해도 주소가 유지됩니다.' },
      ],
    },
    {
      ...BASE.balanced,
      headline: '서버리스 컨테이너 + 관리형 DB',
      tradeoff: '요청이 없다가 처음 들어오면 1~2초 늦을 수 있습니다',
      resources: [
        { addr: 'google_cloud_run_v2_service.app', service: 'Cloud Run', spec: '1 vCPU, 512MiB, 0~3개', monthlyUsd: 6.2, why: '요청이 없을 때는 0개로 줄어 비용이 거의 안 듭니다.' },
        { addr: 'google_sql_database_instance.main', service: 'Cloud SQL PostgreSQL', spec: 'db-f1-micro, 10GB', monthlyUsd: 11.42, why: 'SQLite 대신 씁니다.' },
        { addr: 'google_logging_project_bucket_config.app', service: 'Cloud Logging', spec: '30일 보관', monthlyUsd: 0, why: '무료 한도 안에서 충분합니다.' },
      ],
    },
    {
      ...BASE.roomy,
      headline: '항상 켜진 컨테이너 + 이중화 DB',
      tradeoff: '지금 규모에서는 사양이 많이 남습니다',
      resources: [
        { addr: 'google_cloud_run_v2_service.app', service: 'Cloud Run', spec: '1 vCPU, 1GiB, 1~10개', monthlyUsd: 46.8, why: '최소 1개를 켜 두어 첫 요청 지연이 없습니다.' },
        { addr: 'google_sql_database_instance.main', service: 'Cloud SQL PostgreSQL', spec: '1 vCPU, 3.75GB, HA', monthlyUsd: 104.9, why: '영역 장애 시 자동으로 넘어갑니다.' },
        { addr: 'google_monitoring_alert_policy.app', service: 'Cloud Monitoring', spec: '경보 3개', monthlyUsd: 0, why: '지연, 5xx, DB 연결 경보를 겁니다.' },
      ],
    },
  ],
  azure: [
    {
      ...BASE.lean,
      headline: '가상 서버 한 대 (VM)',
      tradeoff: '서버가 멈추면 복구될 때까지 서비스도 멈춥니다',
      resources: [
        { addr: 'azurerm_linux_virtual_machine.app', service: 'Virtual Machine', spec: 'B1ms', monthlyUsd: 17.52, why: '1 vCPU, 2GB 버스터블로 시연 규모에 맞습니다.' },
        { addr: 'azurerm_managed_disk.data', service: 'Managed Disk', spec: 'Standard SSD 32GB', monthlyUsd: 2.4, why: 'SQLite 파일을 디스크에 둡니다.' },
        { addr: 'azurerm_public_ip.app', service: 'Public IP', spec: '고정 1개', monthlyUsd: 3.65, why: '재시작해도 주소가 유지됩니다.' },
      ],
    },
    {
      ...BASE.balanced,
      headline: '컨테이너 앱 + 관리형 DB',
      tradeoff: '요청이 없다가 처음 들어오면 조금 늦을 수 있습니다',
      resources: [
        { addr: 'azurerm_container_app.app', service: 'Container Apps', spec: '0.5 vCPU, 1GiB, 0~3개', monthlyUsd: 8.1, why: '쓰는 만큼만 비용이 나갑니다.' },
        { addr: 'azurerm_postgresql_flexible_server.main', service: 'PostgreSQL Flexible', spec: 'B1ms, 32GB', monthlyUsd: 20.9, why: 'SQLite 대신 씁니다.' },
        { addr: 'azurerm_log_analytics_workspace.app', service: 'Log Analytics', spec: '30일 보관', monthlyUsd: 2.3, why: '배포 실패 시 AI가 원인을 볼 로그입니다.' },
      ],
    },
    {
      ...BASE.roomy,
      headline: '항상 켜진 컨테이너 + 이중화 DB',
      tradeoff: '지금 규모에서는 사양이 많이 남습니다',
      resources: [
        { addr: 'azurerm_container_app.app', service: 'Container Apps', spec: '1 vCPU, 2GiB, 2~6개', monthlyUsd: 52.6, why: '최소 2개로 한 개가 죽어도 버팁니다.' },
        { addr: 'azurerm_postgresql_flexible_server.main', service: 'PostgreSQL Flexible', spec: 'B2s, 영역 중복 HA', monthlyUsd: 71.5, why: '영역 장애 시 자동으로 넘어갑니다.' },
        { addr: 'azurerm_application_insights.app', service: 'Application Insights', spec: '경보 3개', monthlyUsd: 3.1, why: '지연, 5xx, DB 연결 경보를 겁니다.' },
      ],
    },
  ],
  onprem: [
    {
      ...BASE.lean,
      headline: '앱 컨테이너 하나',
      tradeoff: '서버 전원이나 회선이 끊기면 서비스도 멈춥니다',
      usageNote: 'CPU 0.5, 메모리 512MB 사용',
      resources: [
        { addr: 'service.app', service: '앱 컨테이너', spec: 'restart: always', monthlyUsd: 0, why: '서버에 이미 있는 Docker로 실행합니다.' },
        { addr: 'volume.data', service: '데이터 볼륨', spec: './data 마운트', monthlyUsd: 0, why: 'SQLite 파일을 서버 디스크에 둡니다.' },
      ],
    },
    {
      ...BASE.balanced,
      headline: '앱 + DB + HTTPS 프록시',
      tradeoff: '백업은 서버 디스크에만 남습니다',
      usageNote: 'CPU 1, 메모리 1.5GB 사용',
      resources: [
        { addr: 'service.app', service: '앱 컨테이너', spec: 'restart: always', monthlyUsd: 0, why: '서버에 이미 있는 Docker로 실행합니다.' },
        { addr: 'service.db', service: 'PostgreSQL 컨테이너', spec: 'postgres:16, 볼륨 유지', monthlyUsd: 0, why: 'SQLite 대신 씁니다.' },
        { addr: 'service.proxy', service: 'Caddy', spec: '443, 인증서 자동 갱신', monthlyUsd: 0, why: 'HTTPS를 붙입니다.' },
      ],
    },
    {
      ...BASE.roomy,
      headline: '앱 2개 + DB + 매일 백업',
      tradeoff: '서버 한 대라 하드웨어 장애는 막지 못합니다',
      usageNote: 'CPU 2, 메모리 3GB 사용',
      resources: [
        { addr: 'service.app', service: '앱 컨테이너 × 2', spec: 'Caddy가 나눠 보냄', monthlyUsd: 0, why: '한 개가 죽어도 나머지가 받습니다.' },
        { addr: 'service.db', service: 'PostgreSQL 컨테이너', spec: 'postgres:16', monthlyUsd: 0, why: 'SQLite 대신 씁니다.' },
        { addr: 'service.backup', service: '백업 컨테이너', spec: '매일 03:00 pg_dump, 7일 보관', monthlyUsd: 0, why: '실수로 지운 데이터를 되살릴 수 있습니다.' },
      ],
    },
  ],
}

export const findTier = (p: Provider, t: TierKey) => CATALOG[p].find((x) => x.key === t)!

// ---------- 생성 코드 ----------

const HEAD: Record<Exclude<Provider, 'onprem'>, string> = {
  aws: `provider "aws" {\n  region = var.region\n}`,
  gcp: `provider "google" {\n  project = var.project_id\n  region  = var.region\n}`,
  azure: `provider "azurerm" {\n  features {}\n  subscription_id = var.subscription_id\n}`,
}

function tfMain(p: Exclude<Provider, 'onprem'>, tier: CatalogTier) {
  const blocks = tier.resources.map((r) => {
    const [type, name] = r.addr.split('.')
    return `# ${r.service} — ${r.why}\nresource "${type}" "${name}" {\n  name = "\${var.app_name}-${name}"\n  # ${r.spec}\n}`
  })
  return [HEAD[p], ...blocks].join('\n\n')
}

const TF_VARS = (p: Exclude<Provider, 'onprem'>) => {
  const region = { aws: 'ap-northeast-2', gcp: 'asia-northeast3', azure: 'koreacentral' }[p]
  return `variable "app_name" {\n  type    = string\n  default = "sample-app"\n}\n\nvariable "region" {\n  type    = string\n  default = "${region}"\n}\n\nvariable "image" {\n  type        = string\n  description = "빌드 후 레지스트리에 올린 이미지 주소"\n}`
}

const COMPOSE = (tier: CatalogTier) => {
  const parts = [
    `services:\n  app:\n    image: sample-app:v1\n    restart: always${tier.key === 'roomy' ? '\n    deploy:\n      replicas: 2' : ''}\n    environment:\n      DATABASE_URL: \${DATABASE_URL}${tier.key === 'lean' ? '\n    volumes:\n      - ./data:/app/data\n    ports:\n      - "8080:8000"' : ''}`,
  ]
  if (tier.key !== 'lean') {
    parts.push(`  db:\n    image: postgres:16\n    restart: always\n    volumes:\n      - pgdata:/var/lib/postgresql/data`)
    parts.push(`  proxy:\n    image: caddy:2\n    ports:\n      - "80:80"\n      - "443:443"\n    volumes:\n      - ./Caddyfile:/etc/caddy/Caddyfile`)
  }
  if (tier.key === 'roomy') parts.push(`  backup:\n    image: prodrigestivill/postgres-backup-local\n    environment:\n      SCHEDULE: "0 3 * * *"\n      BACKUP_KEEP_DAYS: 7`)
  if (tier.key !== 'lean') parts.push(`volumes:\n  pgdata:`)
  return parts.join('\n\n')
}

const DEPLOY_SH = `#!/bin/sh
# 서버에서 실행됨. 이미지를 받아서 다시 띄움
set -e
cd \${DEPLOY_PATH}
docker compose pull
docker compose up -d --remove-orphans
curl -sf http://localhost:8080/health`

export function buildFiles(p: Provider, tier: CatalogTier) {
  if (p === 'onprem')
    return [
      { name: 'docker-compose.yml', content: COMPOSE(tier) },
      { name: 'deploy.sh', content: DEPLOY_SH },
    ]
  return [
    { name: 'main.tf', content: tfMain(p, tier) },
    { name: 'variables.tf', content: TF_VARS(p) },
    { name: 'outputs.tf', content: `output "url" {\n  value = local.public_url\n}` },
  ]
}

export function buildPlan(p: Provider, tier: CatalogTier) {
  if (p === 'onprem') {
    const lines = tier.resources.map((r) => `  + ${r.addr.padEnd(16)} # ${r.spec}`)
    return `docker compose 변경 사항 (대상 서버):\n\n${lines.join('\n')}\n\n${tier.resources.length} to create, 0 to change, 0 to remove.`
  }
  const body = tier.resources
    .map((r) => {
      const [type, name] = r.addr.split('.')
      return `  # ${r.addr} will be created\n  + resource "${type}" "${name}" {\n      # ${r.spec}\n    }`
    })
    .join('\n\n')
  return `Terraform will perform the following actions:\n\n${body}\n\nPlan: ${tier.resources.length} to add, 0 to change, 0 to destroy.`
}

// ---------- 배포 로그 ----------

const REGISTRY: Record<Provider, string> = {
  aws: '1234.dkr.ecr.ap-northeast-2.amazonaws.com/sample-app:v1',
  gcp: 'asia-northeast3-docker.pkg.dev/paved-demo/apps/sample-app:v1',
  azure: 'pavedclouds.azurecr.io/sample-app:v1',
  onprem: 'registry.local:5000/sample-app:v1',
}

export function logScript(p: Provider, tier: CatalogTier, host?: string): [number, string][] {
  const head: [number, string][] = [
    [0, '$ docker build -t sample-app:v1 .'],
    [600, 'Step 4/7 : RUN pip install -r requirements.txt'],
    [1500, 'Successfully built 3f9a1c2e'],
    [1900, `$ docker push ${REGISTRY[p]}`],
    [2600, 'v1: digest: sha256:9e1f…  size: 1786'],
  ]
  if (p === 'onprem') {
    return [
      ...head,
      [3000, `$ ssh ${host ?? 'server'} 'sh deploy.sh'`],
      ...tier.resources.map((r, i): [number, string] => [3600 + i * 700, ` Container ${r.addr.replace('service.', 'sample-app-')}  Started`]),
      [6200, '$ curl -sf $URL/health'],
    ]
  }
  return [
    ...head,
    [2900, '$ terraform apply plan.out'],
    ...tier.resources.flatMap((r, i): [number, string][] => [
      [3300 + i * 900, `${r.addr}: Creating...`],
      [3700 + i * 900, `${r.addr}: Creation complete`],
    ]),
    [7000, 'Apply complete! Resources added.'],
    [7300, '$ curl -sf $URL/health'],
  ]
}

export function publicUrl(p: Provider, tier: TierKey, host?: string) {
  if (p === 'onprem') return tier === 'lean' ? `http://${host ?? '192.168.0.24'}:8080` : `https://${host ?? '192.168.0.24'}`
  if (tier === 'lean') return { aws: 'http://3.38.112.47', gcp: 'http://34.64.120.9', azure: 'http://20.194.33.12' }[p]
  return {
    aws: 'https://sample-app-alb-1203.ap-northeast-2.elb.amazonaws.com',
    gcp: 'https://sample-app-3kq2v7-du.a.run.app',
    azure: 'https://sample-app.koreacentral.azurecontainerapps.io',
  }[p]
}
