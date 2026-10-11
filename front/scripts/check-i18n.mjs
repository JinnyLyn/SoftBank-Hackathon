// 일본어 번역이 빠진 화면 문구를 찾는다. 실행: npm run check:i18n --prefix front
// - t('한국어 원문') 으로 감싼 문구가 src/i18n/ja/*.ts 사전에 있는지 확인한다(없으면 일본어 화면에서 한국어로 보임).
// - 상수에 한국어로 두고 그릴 때 t(x.label)로 감싸는 문구는 여기서 확인하지 못한다. 그런 상수를 추가하면 사전에도 같은 원문을 넣는다.
// 사전은 한국어 원문이 키다. 새 문구를 추가했으면 해당 영역 파일(app·steps·api·extra)에 일본어를 넣는다.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const src = fileURLToPath(new URL('../src/', import.meta.url))
const HANGUL = /[가-힣]/

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return name === 'i18n' ? [] : walk(p)
    return /\.(ts|tsx)$/.test(name) ? [p] : []
  })
}

const dict = new Set()
for (const name of readdirSync(join(src, 'i18n', 'ja'))) {
  const text = readFileSync(join(src, 'i18n', 'ja', name), 'utf8')
  for (const m of text.matchAll(/^\s*(['"])((?:\\.|(?!\1).)*)\1\s*:/gm)) dict.add(m[2].replace(/\\(['"])/g, '$1'))
}

const missing = []
for (const file of walk(src)) {
  const code = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '))
  const rel = relative(src, file)
  code.split('\n').forEach((line, i) => {
    const bare = line.replace(/\/\/.*$/, '')
    for (const m of bare.matchAll(/\bt(?:r)?\(\s*(['"])((?:\\.|(?!\1).)*)\1/g)) {
      const key = m[2].replace(/\\(['"])/g, '$1')
      if (HANGUL.test(key) && !dict.has(key)) missing.push(`${rel}:${i + 1}  ${key}`)
    }
  })
}

console.log(`사전 ${dict.size}개`)
if (missing.length) {
  console.log(`\n일본어 사전에 없는 문구 ${missing.length}개:`)
  for (const m of missing) console.log('  ' + m)
  process.exit(1)
}
console.log('\n사전에 없는 t() 문구 없음')
