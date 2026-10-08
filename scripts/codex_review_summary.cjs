'use strict';

const MARKER = '<!-- paved-codex-review-summary -->';
const CODEX = 'chatgpt-codex-connector[bot]';
const TICK = String.fromCharCode(96);
const MAX_BODY = 60000;

function isCodex(item) {
  return item.user?.login === CODEX && item.user?.type === 'Bot';
}

function newest(items) {
  return [...items].sort((a, b) =>
    Date.parse(b.updated_at || b.submitted_at || b.created_at) -
      Date.parse(a.updated_at || a.submitted_at || a.created_at) ||
    Number(b.id) - Number(a.id))[0];
}

function matchesCommit(full, short) {
  return Boolean(full && short && full.startsWith(short));
}

function codeReviewStatus(comment) {
  if (!isCodex(comment) ||
      !comment.body?.includes('<!-- codex-pull-request-review-summary -->')) return null;
  const row = comment.body.split('\n').find(line =>
    /^\|/.test(line) && /\*\*Code Review\*\*/.test(line));
  if (!row) return null;
  const cells = row.split('|');
  const commit = cells[3]?.match(/[a-f0-9]{7,40}/)?.[0];
  if (!commit) return null;
  const status = cells[2].replace(/<[^>]*>.*?<\/[^>]*>/g, '')
    .replace(/<[^>]*>/g, '').replace(/\*/g, '').trim();
  let state = 'unknown';
  if (/\bCompleted\b/i.test(status)) state = 'completed';
  else if (/\b(Failed|Error|Cancelled|Canceled|Skipped)\b/i.test(status)) state = 'failed';
  else if (/\b(Running|In progress|Queued|Pending)\b/i.test(status)) state = 'running';
  return { ...comment, commit, state };
}

// Preserve Markdown while preventing copied mentions and hidden HTML from firing.
function safeMarkdown(value) {
  return String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/@/g, '&#64;');
}

function code(value) {
  return TICK + String(value || '').replaceAll(TICK, '').replace(/[\r\n|]/g, ' ') + TICK;
}

function findingDetails(comment) {
  const lines = (comment.body || '').trim().split('\n');
  const heading = lines.shift() || 'Codex 지적';
  const priority = heading.match(/\bP[0-3]\b/)?.[0] || '미분류';
  const title = heading.replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/<[^>]*>/g, '').replace(/\*\*/g, '').replace(/\[P[0-3]\]/g, '')
    .trim() || 'Codex 지적';
  const detail = lines.join('\n').replace(/\n*Useful\? React with[^\n]*\.?\s*$/i, '').trim();
  return { priority, title: safeMarkdown(title), detail: safeMarkdown(detail) };
}

function renderSummary({ pull, reviews, issueComments, reviewComments, threadStates }) {
  const codeReviews = reviews.filter(review => isCodex(review) &&
    /^#{2,3}\s+[^\n]*Codex Review\b/m.test(review.body || '') && review.state !== 'PENDING');
  const reviewById = new Map(codeReviews.map(review => [review.id, review]));
  const latestReview = newest(codeReviews);
  const tracker = newest(issueComments.map(codeReviewStatus).filter(Boolean));
  // A newer submission may arrive before the completion tracker is updated.
  const evidence = latestReview && (!tracker ||
    Date.parse(latestReview.submitted_at) > Date.parse(tracker.updated_at || tracker.created_at))
    ? { commit: latestReview.commit_id, state: 'submitted', html_url: latestReview.html_url }
    : tracker;
  const currentEvidence = evidence && matchesCommit(pull.head.sha, evidence.commit);
  const findings = reviewComments.filter(comment => isCodex(comment) &&
    !comment.in_reply_to_id && reviewById.has(comment.pull_request_review_id))
    .map(comment => ({
      ...comment,
      ...findingDetails(comment),
      commit: reviewById.get(comment.pull_request_review_id).commit_id,
      dismissed: reviewById.get(comment.pull_request_review_id).state === 'DISMISSED',
      ...threadStates.get(comment.id),
    }));
  const current = findings.filter(finding => finding.commit === pull.head.sha);
  const previous = findings.filter(finding => finding.commit !== pull.head.sha);
  const open = current.filter(finding => !finding.resolved && !finding.dismissed);
  const closed = current.filter(finding => !open.includes(finding));

  let status = '⏳ 현재 커밋의 리뷰 완료를 아직 확인하지 못했습니다.';
  if (evidence && !currentEvidence) status = '⚠️ 이전 커밋의 리뷰입니다. 현재 커밋의 리뷰 완료를 확인해야 합니다.';
  else if (evidence?.state === 'completed') status = '✅ 리뷰 실행 완료';
  else if (evidence?.state === 'failed') status = '⚠️ 리뷰 실패·취소·건너뜀 — 완료된 결과가 아닙니다.';
  else if (evidence?.state === 'running') status = '⏳ 리뷰 진행 중·대기 중';
  else if (evidence?.state === 'submitted') status = '📝 리뷰가 게시됐습니다. 완료 표시는 아직 확인되지 않았습니다.';

  const priorityCounts = ['P0', 'P1', 'P2', 'P3', '미분류']
    .map(priority => [priority, open.filter(finding => finding.priority === priority).length])
    .filter(([, count]) => count).map(([priority, count]) => priority + ' ' + count + '건').join(' · ');
  const lines = [
    MARKER,
    '## Codex 코드 리뷰 상세 요약',
    '',
    '**상태:** ' + status,
    '',
    '| 항목 | 내용 |',
    '| --- | --- |',
    '| 현재 PR 커밋 | ' + code(pull.head.sha.slice(0, 10)) + ' |',
    '| 최근 리뷰 기록의 커밋 | ' + (evidence ? code(evidence.commit.slice(0, 10)) : '기록 없음') + ' |',
    '| 현재 커밋에 게시된 지적 | ' + current.length + '건 |',
    '| 현재 커밋의 미해결 지적 | ' + open.length + '건' + (priorityCounts ? ' — ' + priorityCounts : '') + ' |',
    '| 해결 표시·철회된 리뷰의 지적 | ' + closed.length + '건 |',
    '| 이전 커밋에 게시된 지적 | ' + previous.length + '건 |',
    '',
    '### 결론',
    '',
  ];
  if (!currentEvidence || evidence.state !== 'completed') {
    lines.push('현재 커밋에 대해 완료된 리뷰 결과가 확인되지 않았습니다. 게시된 지적은 아래에서 확인하세요.');
  } else if (open.length) {
    lines.push('현재 커밋에 미해결 지적 ' + open.length + '건이 게시돼 있습니다. 수정하거나 처리 방향을 검토하세요.');
  } else {
    lines.push('현재 커밋에 게시된 미해결 지적은 없습니다.');
  }
  if (previous.some(finding => !finding.resolved && !finding.dismissed)) {
    lines.push('', '이전 커밋에도 해결 표시가 없는 지적이 있습니다. 위치가 오래됐다는 표시만으로 수정 여부를 판단하지 마세요.');
  }
  lines.push('', '완료는 실행 상태이며 코드 승인이나 머지 가능 판정이 아닙니다. 대화의 해결 표시도 코드 수정 검증을 뜻하지 않습니다.',
    '', '### 현재 커밋의 미해결 지적', '');

  let omitted = 0;
  function appendFinding(finding, index) {
    const location = finding.path + ':' + (finding.line || finding.original_line || '위치 변경');
    const state = finding.dismissed ? '리뷰 철회됨' : finding.resolved ? '해결 표시됨'
      : finding.outdated ? '오래된 코드 위치 · 수정 여부 확인 필요' : finding.resolved === false ? '미해결'
        : '대화 상태 확인 불가';
    const block = [
      '#### ' + index + '. [' + finding.priority + '] ' + finding.title,
      '',
      '- 위치: ' + code(location),
      '- 대화 상태: **' + state + '**',
      '- 리뷰 커밋: ' + code(finding.commit.slice(0, 10)),
      '- [원문과 대화 보기](' + finding.html_url + ')',
      '',
      finding.detail || '상세 내용은 원문을 확인하세요.',
      '',
    ];
    if (lines.join('\n').length + block.join('\n').length > MAX_BODY - 3000) omitted++;
    else lines.push(...block);
  }
  if (!open.length) lines.push('게시된 항목 없음', '');
  open.forEach((finding, index) => appendFinding(finding, index + 1));
  function collapsed(title, items) {
    if (!items.length) return;
    lines.push('<details>', '<summary>' + title + ' (' + items.length + '건)</summary>', '');
    items.forEach((finding, index) => appendFinding(finding, index + 1));
    lines.push('</details>', '');
  }
  collapsed('현재 커밋: 해결 표시·철회된 리뷰', closed);
  collapsed('이전 커밋의 지적 — 현재 코드에서 별도 확인 필요', previous);
  if (omitted) lines.push('댓글 길이 제한으로 ' + omitted + '건의 상세 내용을 생략했습니다. PR의 Files changed에서 원문을 확인하세요.', '');
  if (evidence?.html_url) lines.push('[최근 Codex 리뷰 기록](' + evidence.html_url + ')', '');
  lines.push('이 댓글은 GitHub에 게시된 Codex Code Review와 대화 상태를 모은 요약입니다. 추가 AI 호출이나 테스트 실행은 하지 않습니다.',
    '대화 해결·재개만으로는 자동 갱신되지 않습니다. 상태를 다시 확인하려면 Actions → Codex Review Summary → Run workflow에서 PR 번호를 입력하세요.',
    '갱신 시점: ' + new Date().toISOString());
  return lines.join('\n');
}

async function readThreadStates(github, { owner, repo, number }) {
  const states = new Map();
  let cursor = null;
  do {
    const result = await github.graphql(
      'query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {' +
      ' repository(owner: $owner, name: $repo) { pullRequest(number: $number) {' +
      ' reviewThreads(first: 100, after: $cursor) {' +
      ' nodes { isResolved isOutdated comments(first: 1) { nodes { databaseId } } }' +
      ' pageInfo { hasNextPage endCursor } } } } }',
      { owner, repo, number, cursor });
    const page = result.repository.pullRequest.reviewThreads;
    for (const thread of page.nodes) {
      const id = thread.comments.nodes[0]?.databaseId;
      if (id) states.set(id, { resolved: thread.isResolved, outdated: thread.isOutdated });
    }
    cursor = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : null;
  } while (cursor);
  return states;
}

async function run({ github, context, core }) {
  const rawNumber = context.payload.inputs?.pr_number ||
    context.payload.pull_request?.number || context.payload.issue?.number;
  if (!/^[1-9]\d*$/.test(String(rawNumber || ''))) throw new Error('올바른 PR 번호가 필요합니다.');
  const number = Number(rawNumber);
  if (!Number.isSafeInteger(number)) throw new Error('PR 번호가 너무 큽니다.');
  const { owner, repo } = context.repo;
  const params = { owner, repo, pull_number: number };
  const { data: pull } = await github.rest.pulls.get(params);
  if (pull.state !== 'open' || pull.base.ref !== context.payload.repository.default_branch) {
    core.info('열려 있는 기본 브랜치 대상 PR만 요약합니다.');
    return;
  }
  // Review events from forks have a read-only token. Manual runs use main's token.
  if (pull.head.repo?.full_name !== owner + '/' + repo && context.eventName !== 'workflow_dispatch') {
    core.info('fork PR의 자동 요약은 지원하지 않습니다. 기본 브랜치에서 수동 갱신하세요.');
    return;
  }
  const [reviews, reviewComments, issueComments, threadStates] = await Promise.all([
    github.paginate(github.rest.pulls.listReviews, { ...params, per_page: 100 }),
    github.paginate(github.rest.pulls.listReviewComments, { ...params, per_page: 100 }),
    github.paginate(github.rest.issues.listComments, { owner, repo, issue_number: number, per_page: 100 }),
    readThreadStates(github, { owner, repo, number }),
  ]);
  const body = renderSummary({ pull, reviews, reviewComments, issueComments, threadStates });
  // Do not write a result for a head that changed while the APIs were being read.
  const { data: latestPull } = await github.rest.pulls.get(params);
  if (latestPull.head.sha !== pull.head.sha || latestPull.state !== 'open') {
    core.info('PR 상태가 바뀌어 요약 갱신을 건너뜁니다. 다음 이벤트에서 다시 확인합니다.');
    return;
  }
  const existing = issueComments.find(comment =>
    comment.user?.login === 'github-actions[bot]' && comment.user?.type === 'Bot' &&
    comment.body?.startsWith(MARKER));
  if (existing) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
  } else {
    await github.rest.issues.createComment({ owner, repo, issue_number: number, body });
  }
}

module.exports = { run, renderSummary, readThreadStates, codeReviewStatus, MARKER };
