import type {
  Analysis,
  CostLine,
  DeployRecord,
  DeployStatus,
  PlanSummary,
  ScaleInput,
  Target,
} from '../types'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// 파일명에 fail 이 들어가면 헬스체크 실패 시나리오로 진행 (데모용)
let failScenario = false
let deployStartedAt = 0
let deployTargets: Target[] = []

export async function analyze(file: File, _scale: ScaleInput): Promise<Analysis> {
  failScenario = /fail/i.test(file.name)
  await wait(1400)
  return {
    projectId: 'p_' + Math.random().toString(36).slice(2, 8),
    framework: 'FastAPI',
    port: 8000,
    db: 'SQLite 감지',
    fileStorage: '사용 안 함',
    notice: {
      title: 'SQLite는 컨테이너 재시작 시 데이터가 사라집니다. RDS 전환을 제안합니다.',
      detail: 'db_mode: create (AWS 배포 시 RDS 신규 생성, 온프레미스는 컨테이너 볼륨 유지)',
    },
    evidence: [
      'requirements.txt에 fastapi, uvicorn이 있음',
      'app/db.py에서 sqlite3.connect 호출을 찾음',
      '.env에 DATABASE_URL 없음, 외부 DB 주소 없음',
    ],
  }
}

export async function estimate(
  _projectId: string,
  targets: Target[],
  scale: ScaleInput,
): Promise<CostLine[]> {
  await wait(500)
  const big = scale.expectedUsers === '~10,000' || scale.expectedUsers === '10,000+'
  const lines: CostLine[] = []
  if (targets.includes('aws')) {
    lines.push(
      {
        target: 'aws',
        resource: 'ECS 서비스',
        spec: big ? 'cpu 512, memory 1024 × 2' : 'cpu 256, memory 512',
        monthlyUsd: big ? 36.04 : 9.01,
      },
      {
        target: 'aws',
        resource: 'RDS',
        spec: big ? 'db.t4g.small, 20GB' : '신규 생성 (db_mode: create)',
        monthlyUsd: big ? 28.47 : 14.6,
      },
      { target: 'aws', resource: '공용 ALB, 로그 그룹', spec: '미리 만든 공용 기반 사용', monthlyUsd: 2.1 },
      { target: 'aws', resource: 'ECR', spec: '이미지 1개, 약 180MB', monthlyUsd: 0.02 },
    )
  }
  if (targets.includes('onprem')) {
    lines.push(
      { target: 'onprem', resource: '앱 컨테이너', spec: 'Docker Compose, restart: always', monthlyUsd: null },
      { target: 'onprem', resource: 'SQLite 볼륨', spec: './data 마운트', monthlyUsd: null },
    )
  }
  return lines
}

const AWS_PLAN = `  # aws_ecr_repository.app will be created
  + resource "aws_ecr_repository" "app" {
      + name                 = "sample-app"
      + image_tag_mutability = "MUTABLE"
    }

  # aws_db_instance.main will be created
  + resource "aws_db_instance" "main" {
      + engine              = "postgres"
      + instance_class      = "db.t4g.micro"
      + allocated_storage   = 20
      + publicly_accessible = false
    }

  # aws_ecs_service.app will be created
  + resource "aws_ecs_service" "app" {
      + desired_count = 1
      + launch_type   = "FARGATE"
    }

  # aws_lb_listener_rule.app will be updated in-place
  ~ resource "aws_lb_listener_rule" "app" {
      ~ priority = 110 -> 120
    }

Plan: 9 to add, 1 to change, 0 to destroy.`

const ONPREM_COMPOSE = `# 온프레미스는 Terraform 대신 docker-compose.yml 을 SSH로 전달합니다.
services:
  app:
    image: sample-app:v4
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]
    restart: always`

export async function plan(_projectId: string, targets: Target[]): Promise<PlanSummary> {
  await wait(700)
  const aws = targets.includes('aws')
  const parts = []
  if (aws) parts.push(AWS_PLAN)
  if (targets.includes('onprem')) parts.push(ONPREM_COMPOSE)
  return { add: aws ? 9 : 0, change: aws ? 1 : 0, destroy: 0, text: parts.join('\n\n') }
}

export async function approve(_projectId: string, targets: Target[]): Promise<void> {
  await wait(300)
  // 실패 후 다시 승인하면 AI 수정안이 반영된 것으로 보고 성공시킴
  if (deployStartedAt) failScenario = false
  deployStartedAt = Date.now()
  deployTargets = targets
}

// 경과 시간에 따라 단계가 진행되는 것처럼 흉내
export async function status(_projectId: string): Promise<DeployStatus> {
  await wait(150)
  const t = Date.now() - deployStartedAt
  const steps: DeployStatus['steps'] = {
    upload: 'done',
    analyze: 'done',
    approve: 'done',
    build: 'waiting',
    deploy: 'waiting',
    health: 'waiting',
  }
  const urls: DeployStatus['urls'] = {}

  if (t < 2500) {
    steps.build = 'active'
    return { steps, urls }
  }
  steps.build = 'done'
  if (t < 5500) {
    steps.deploy = 'active'
    return { steps, urls }
  }
  steps.deploy = 'done'
  if (t < 7000) {
    steps.health = 'active'
    return { steps, urls }
  }
  if (failScenario) {
    steps.health = 'failed'
    return {
      steps,
      urls,
      diagnosis: {
        cause: '앱이 127.0.0.1에서만 listen 하고 있어서 ALB 헬스체크 요청이 컨테이너에 닿지 않습니다.',
        fix: 'Dockerfile CMD를 uvicorn app.main:app --host 0.0.0.0 --port 8000 으로 바꾼 뒤 재배포합니다.',
        log: `[ecs] service sample-app: task stopped (Essential container exited)
[alb] target 10.0.3.41:8000 unhealthy: Request timed out
[app] INFO:     Uvicorn running on http://127.0.0.1:8000`,
      },
    }
  }
  steps.health = 'done'
  if (deployTargets.includes('aws')) urls.aws = 'https://sample-app.oneship.dev'
  if (deployTargets.includes('onprem')) urls.onprem = 'http://192.168.0.24:8000'
  return { steps, urls }
}

export async function history(): Promise<DeployRecord[]> {
  await wait(200)
  return [
    { id: 'd6', app: 'sample-app', version: 'v3', target: 'aws', method: 'Terraform', status: 'success', createdAt: '2026-10-07 14:12' },
    { id: 'd5', app: 'sample-app', version: 'v3', target: 'onprem', method: 'Docker Compose', status: 'success', createdAt: '2026-10-07 14:10' },
    { id: 'd4', app: 'sample-app', version: 'v2', target: 'aws', method: 'Terraform', status: 'failed', note: '헬스체크 실패, v1로 롤백', createdAt: '2026-10-06 21:47' },
    { id: 'd3', app: 'sample-app', version: 'v1', target: 'aws', method: 'Terraform', status: 'success', createdAt: '2026-10-05 18:03' },
    { id: 'd2', app: 'todo-api', version: 'v2', target: 'onprem', method: 'Docker Compose', status: 'success', createdAt: '2026-10-04 11:30' },
    { id: 'd1', app: 'todo-api', version: 'v1', target: 'onprem', method: 'Docker Compose', status: 'failed', note: '포트 8000 사용 중', createdAt: '2026-10-04 11:02' },
  ]
}
