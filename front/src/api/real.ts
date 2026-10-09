// 실제 백엔드(FastAPI, back/API.md) 연동.
// 백엔드 응답을 화면 타입(types.ts)으로 바꾸는 일은 여기서만 함
import type { Analysis, Finding, ScaleInput, Source } from '../types'
import { ApiError, req, reqOrNull, send } from './http'
import { sourceName } from '../format'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 결과가 생길 때까지 주기적으로 확인. 시간이 지나면 이유를 담아 실패 */
async function poll<T>(fn: () => Promise<T | null>, opts: { intervalMs: number; timeoutMs: number; what: string }) {
  const until = Date.now() + opts.timeoutMs
  for (;;) {
    const v = await fn()
    if (v) return v
    if (Date.now() > until)
      throw new ApiError(408, `${opts.what}가 ${Math.round(opts.timeoutMs / 60000)}분 안에 준비되지 않았습니다. 잠시 뒤 다시 시도해 주세요.`)
    await wait(opts.intervalMs)
  }
}

// ---------- 프로젝트 ----------

interface ProjectOut {
  id: string
  name: string
  source_filename: string
  source_sha256: string
  source_size_bytes: number
  source_type: 'zip' | 'github'
  source_url: string | null
  source_ref: string | null
  created_at: string
}

/** ZIP은 원본 바이트를 그대로, GitHub는 주소와 ref를 JSON으로 */
function createProject(source: Source): Promise<ProjectOut> {
  const name = sourceName(source)
  if (source.kind === 'zip') {
    const q = new URLSearchParams({ name, filename: source.file.name })
    return req<ProjectOut>(`/projects?${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: source.file,
    })
  }
  return req<ProjectOut>(
    '/projects/github',
    send('POST', { name, repository_url: source.url, ref: source.branch || undefined }),
  )
}

// ---------- 분석 ----------

interface AnalysisOut {
  id: string
  project_id: string
  schema_version: string
  source_sha256: string
  result: Record<string, unknown>
  created_at: string
}

// 분석 결과(result)의 필드는 LLM 담당과 합의 중이라 백엔드가 고정하지 않음.
// 화면 형태(stack/findings/evidence)를 우선 읽고, 없으면 최상위 값들을 스택 표로 보여 줌
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))

function toAnalysis(out: AnalysisOut): Analysis {
  const r = out.result

  const stack = Array.isArray(r.stack)
    ? r.stack.filter(isObj).map((s) => ({ label: str(s.label), value: str(s.value) }))
    : Object.entries(r)
        .filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v))
        .map(([k, v]) => ({ label: k, value: str(v) }))

  const findings: Finding[] = (Array.isArray(r.findings) ? r.findings : []).filter(isObj).map((f) => ({
    level: f.level === 'warn' || f.level === 'warning' ? 'warn' : 'info',
    title: str(f.title ?? f.message),
    detail: str(f.detail ?? f.description),
  }))

  const evidence = (Array.isArray(r.evidence) ? r.evidence : []).map((e) =>
    isObj(e) ? [str(e.file) + (e.line ? `:${e.line}` : ''), str(e.text ?? e.reason)].filter(Boolean).join(' — ') : str(e),
  )

  return { projectId: out.project_id, stack, findings, evidence }
}

/**
 * 소스를 등록하고, 분석 모듈이 결과를 기록할 때까지 기다림.
 * 백엔드 API는 LLM 분석을 직접 돌리지 않음 → 분석 담당 모듈이 POST /analyses 로 결과를 남겨야 끝남
 * 사용 규모·예산(scale)은 백엔드 API에 받는 곳이 없어 화면에서 추천을 거를 때만 씀
 */
export async function analyze(source: Source, _scale: ScaleInput): Promise<Analysis> {
  const project = await createProject(source)
  const out = await poll(() => reqOrNull<AnalysisOut>(`/projects/${project.id}/analyses/latest`), {
    intervalMs: 2000,
    timeoutMs: 5 * 60 * 1000,
    what: '분석 결과',
  })
  return toAnalysis(out)
}
