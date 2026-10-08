'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { run, renderSummary, readThreadStates, MARKER } = require('./codex_review_summary.cjs');
const TICK = String.fromCharCode(96);
const HEAD = '93a4b78470c6dc3dfa1f39ec911ded45d012c9b9';
const OLD = '8132e22461b75f863b156e9448ec7dc382bc26ad';
const bot = { login: 'chatgpt-codex-connector[bot]', type: 'Bot' };

function review(id = 1, commit = HEAD) {
  return { id, user: bot, commit_id: commit, state: 'COMMENTED',
    body: '### 💡 Codex Review', submitted_at: '2026-10-08T15:36:41Z',
    html_url: 'https://github.com/example/repo/pull/4#pullrequestreview-' + id };
}

function tracker(state = 'Completed', commit = HEAD, id = 10) {
  return { id, user: bot, updated_at: '2026-10-08T15:36:57Z',
    html_url: 'https://github.com/example/repo/pull/4#issuecomment-' + id,
    body: '<!-- codex-pull-request-review-summary -->\n' +
      '| 📝 **Code Review** | ✅ **' + state + '** <relative-time datetime="2026-10-08T15:36:45Z">date</relative-time> | ' +
      TICK + commit.slice(0, 7) + TICK + ' | PR opened |' };
}

function finding(id = 101, priority = 'P1', reviewId = 1) {
  return { id, user: bot, pull_request_review_id: reviewId, path: 'sample-back/app/main.py',
    line: 210, original_line: 210, html_url: 'https://github.com/example/repo/pull/4#discussion_r' + id,
    body: '**<sub><sub>![' + priority + ' Badge](https://img.shields.io/badge/' +
      priority + '-orange)</sub></sub> Finding ' + id + '**\n\n' +
      'Detailed explanation ' + id + '.\n\nUseful? React with 👍 / 👎.' };
}

function snapshot(overrides = {}) {
  return { pull: { head: { sha: HEAD } }, reviews: [review()],
    issueComments: [tracker()], reviewComments: [finding()],
    threadStates: new Map([[101, { resolved: false, outdated: false }]]), ...overrides };
}

test('completed review still exposes findings, priorities and full details', () => {
  const body = renderSummary(snapshot({
    reviewComments: [finding(), finding(102, 'P2')],
    threadStates: new Map([[101, { resolved: false, outdated: false }],
      [102, { resolved: true, outdated: false }]]),
  }));
  assert.match(body, /리뷰 실행 완료/);
  assert.match(body, /미해결 지적 \| 1건 — P1 1건/);
  assert.match(body, /해결 표시·철회된 리뷰의 지적 \| 1건/);
  assert.match(body, /Detailed explanation 101/);
  assert.match(body, /Detailed explanation 102/);
  assert.match(body, /discussion_r101/);
  assert.match(body, /코드 승인이나 머지 가능 판정이 아닙니다/);
  assert.doesNotMatch(body, /Useful\?|img\.shields/);
});

test('pending, failed and unknown results never imply a completed clean review', () => {
  for (const state of ['Running', 'Queued', 'Failed', 'Cancelled', 'Skipped', 'Unexpected']) {
    const body = renderSummary(snapshot({
      reviews: [], reviewComments: [], issueComments: [tracker(state)],
    }));
    assert.doesNotMatch(body, /✅ 리뷰 실행 완료/);
    assert.match(body, /완료된 리뷰 결과가 확인되지 않았습니다/);
  }
  const empty = renderSummary(snapshot({ reviews: [], reviewComments: [], issueComments: [] }));
  assert.match(empty, /기록 없음/);
  assert.doesNotMatch(empty, /미해결 지적은 없습니다/);
});

test('a completion on the current commit can report zero published findings', () => {
  const body = renderSummary(snapshot({ reviews: [], reviewComments: [] }));
  assert.match(body, /✅ 리뷰 실행 완료/);
  assert.match(body, /현재 커밋에 게시된 미해결 지적은 없습니다/);
  assert.match(body, /코드 승인이나 머지 가능 판정이 아닙니다/);
});

test('a push makes the previous review stale even when its tracker says completed', () => {
  const body = renderSummary(snapshot({
    pull: { head: { sha: OLD } },
    threadStates: new Map([[101, { resolved: false, outdated: true }]]),
  }));
  assert.match(body, /이전 커밋의 리뷰입니다/);
  assert.match(body, /현재 커밋에 게시된 지적 \| 0건/);
  assert.match(body, /이전 커밋에 게시된 지적 \| 1건/);
  assert.match(body, /이전 커밋에도 해결 표시가 없는 지적/);
  assert.doesNotMatch(body, /✅ 리뷰 실행 완료/);
});

test('newer completion does not mix older-commit findings into the current commit', () => {
  const body = renderSummary(snapshot({ reviews: [review(1, OLD)] }));
  assert.match(body, /현재 커밋에 게시된 지적 \| 0건/);
  assert.match(body, /이전 커밋에 게시된 지적 \| 1건/);
  assert.match(body, /Detailed explanation 101/);
});

test('an earlier unresolved finding remains visible after another review on the same head', () => {
  const body = renderSummary(snapshot({
    reviews: [review(), { ...review(2), submitted_at: '2026-10-08T15:36:42Z' }],
    reviewComments: [finding(), finding(102, 'P2', 2)],
    threadStates: new Map([[101, { resolved: false, outdated: false }],
      [102, { resolved: false, outdated: false }]]),
  }));
  assert.match(body, /미해결 지적 \| 2건 — P1 1건 · P2 1건/);
});

test('a submission newer than the tracker waits for new completion evidence', () => {
  const body = renderSummary(snapshot({
    reviews: [{ ...review(), submitted_at: '2026-10-08T16:00:00Z' }],
  }));
  assert.match(body, /완료 표시는 아직 확인되지 않았습니다/);
  assert.doesNotMatch(body, /✅ 리뷰 실행 완료/);
});

test('human impersonation, replies and Security Review do not become Code Review findings', () => {
  const security = { ...review(2), body: '### 🔒 Codex Security Review' };
  const fakeTracker = { ...tracker(), user: { login: bot.login, type: 'User' } };
  const body = renderSummary(snapshot({
    reviews: [review(), security], issueComments: [fakeTracker],
    reviewComments: [finding(), { ...finding(102), in_reply_to_id: 101 },
      finding(103, 'P1', 2), { ...finding(104), user: { login: 'someone', type: 'User' } }],
  }));
  assert.match(body, /현재 커밋에 게시된 지적 \| 1건/);
  assert.doesNotMatch(body, /Detailed explanation 10[234]|✅ 리뷰 실행 완료/);
});

test('copied mentions and raw HTML are neutralized', () => {
  const body = renderSummary(snapshot({
    reviewComments: [{ ...finding(), body: '**[P1] Test**\n\n@team <script>alert(1)</script>\n' +
      '<!-- hidden -->\n\n~~~python\nprint("example")\n~~~' }],
  }));
  assert.doesNotMatch(body, /@team|<script>|<!-- hidden -->/);
  assert.match(body, /&#64;team &lt;script&gt;/);
  assert.match(body, /~~~python/);
});

test('an outdated unresolved location remains an active finding while dismissed reviews are separate', () => {
  const body = renderSummary(snapshot({
    reviews: [{ ...review(), state: 'DISMISSED' }, review(2)],
    reviewComments: [finding(), finding(102, 'P2', 2)],
    threadStates: new Map([[101, { resolved: false, outdated: false }],
      [102, { resolved: false, outdated: true }]]),
  }));
  assert.match(body, /미해결 지적 \| 1건 — P2 1건/);
  assert.match(body, /리뷰 철회됨/);
  assert.match(body, /오래된 코드 위치 · 수정 여부 확인 필요/);
});

test('large reports respect the GitHub comment limit and link to originals', () => {
  const comments = Array.from({ length: 80 }, (_, index) => ({
    ...finding(index + 101), body: '**[P1] Large finding**\n\n' + 'detail '.repeat(1000),
  }));
  const body = renderSummary(snapshot({ reviewComments: comments }));
  assert.ok(body.length < 60000);
  assert.match(body, /댓글 길이 제한/);
  assert.match(body, /discussion_r101/);
});

test('thread states are paginated beyond the first 100 threads', async () => {
  const calls = [];
  const github = { graphql: async (_, variables) => {
    calls.push(variables.cursor);
    return { repository: { pullRequest: { reviewThreads: {
      nodes: [{ isResolved: Boolean(variables.cursor), isOutdated: false,
        comments: { nodes: [{ databaseId: variables.cursor ? 102 : 101 }] } }],
      pageInfo: { hasNextPage: !variables.cursor, endCursor: 'next' },
    } } } };
  } };
  const states = await readThreadStates(github, { owner: 'example', repo: 'repo', number: 4 });
  assert.deepEqual(calls, [null, 'next']);
  assert.equal(states.get(101).resolved, false);
  assert.equal(states.get(102).resolved, true);
});

function fakeApi({ headChanged = false, existing = true, closed = false } = {}) {
  const writes = [];
  let reads = 0;
  const pull = { head: { sha: HEAD, repo: { full_name: 'example/repo' } },
    base: { ref: 'main' }, state: closed ? 'closed' : 'open' };
  const github = {
    rest: {
      pulls: {
        get: async () => ({ data: { ...pull, head: {
          ...pull.head, sha: headChanged && reads++ ? OLD : HEAD,
        } } }),
        listReviews: 'reviews', listReviewComments: 'reviewComments',
      },
      issues: {
        listComments: 'issueComments',
        updateComment: async params => writes.push(['update', params]),
        createComment: async params => writes.push(['create', params]),
      },
    },
    paginate: async endpoint => {
      if (endpoint === 'reviews') return [review()];
      if (endpoint === 'reviewComments') return [finding()];
      return [tracker(), ...(existing ? [{ id: 90, body: MARKER + '\nold',
        user: { login: 'github-actions[bot]', type: 'Bot' } }] : []),
      { id: 89, body: MARKER + '\nspoofed', user: { login: 'someone', type: 'User' } }];
    },
    graphql: async () => ({ repository: { pullRequest: { reviewThreads: {
      nodes: [{ isResolved: false, isOutdated: false,
        comments: { nodes: [{ databaseId: 101 }] } }],
      pageInfo: { hasNextPage: false },
    } } } }),
  };
  const context = { repo: { owner: 'example', repo: 'repo' }, eventName: 'workflow_dispatch',
    payload: { inputs: { pr_number: '4' }, repository: { default_branch: 'main' } } };
  return { github, context, core: { info() {} }, writes };
}

test('reruns update the existing bot summary and do not duplicate comments', async () => {
  const api = fakeApi();
  await run(api);
  await run(api);
  assert.deepEqual(api.writes.map(([method]) => method), ['update', 'update']);
  assert.equal(api.writes[0][1].comment_id, 90);
});

test('first run creates a summary and ignores a human-spoofed marker', async () => {
  const api = fakeApi({ existing: false });
  await run(api);
  assert.equal(api.writes[0][0], 'create');
  assert.equal(api.writes[0][1].issue_number, 4);
});

test('a moving head or closed PR does not receive a stale write', async () => {
  for (const options of [{ headChanged: true }, { closed: true }]) {
    const api = fakeApi(options);
    await run(api);
    assert.equal(api.writes.length, 0);
  }
});

test('invalid manual PR input fails before any API access or write', async () => {
  for (const prNumber of ['-1', '1; echo secret', '0', '9007199254740993']) {
    const api = fakeApi();
    api.context.payload.inputs.pr_number = prNumber;
    await assert.rejects(() => run(api), /PR 번호/);
    assert.equal(api.writes.length, 0);
  }
});

test('fork review events skip writes while manual main runs can refresh them', async () => {
  for (const eventName of ['pull_request_target', 'pull_request_review', 'pull_request_review_comment', 'issue_comment', 'workflow_dispatch']) {
    const api = fakeApi();
    api.context.eventName = eventName;
    const getPull = api.github.rest.pulls.get;
    api.github.rest.pulls.get = async params => {
      const result = await getPull(params);
      result.data.head.repo = { full_name: 'contributor/repo' };
      return result;
    };
    await run(api);
    assert.equal(api.writes.length, eventName === 'workflow_dispatch' ? 1 : 0);
  }
});

// Execute the actual github-script entry point, including its first-install guard.
const workflow = fs.readFileSync(path.join(__dirname, '../.github/workflows/codex-review-summary.yml'), 'utf8');
const entryPoint = workflow.match(/          script: \|\n([\s\S]*)$/)[1]
  .split('\n').map(line => line.slice(12)).join('\n');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const invokeEntryPoint = new AsyncFunction('require', 'github', 'context', 'core', entryPoint);

test('first-install review events skip cleanly when main has no summary workflow', async () => {
  let notices = 0;
  await invokeEntryPoint(name => {
    assert.equal(name, 'node:fs', 'must not load PR code or the absent module');
    return { existsSync: () => false };
  }, {}, {}, { notice: () => notices++ });
  assert.equal(notices, 1);
});

test('installed workflow invokes the summary module', async () => {
  let calls = 0;
  const github = {};
  const context = {};
  const core = {};
  await invokeEntryPoint(name => name === 'node:fs' ? { existsSync: () => true } : {
    run: async args => { calls++; assert.deepEqual(args, { github, context, core }); },
  }, github, context, core);
  assert.equal(calls, 1);
});

test('an installed workflow with a missing script still fails rather than hiding a regression', async () => {
  await assert.rejects(() => invokeEntryPoint(name => {
    if (name === 'node:fs') return { existsSync: () => true };
    throw new Error('MODULE_NOT_FOUND');
  }, {}, {}, {}), /MODULE_NOT_FOUND/);
});
