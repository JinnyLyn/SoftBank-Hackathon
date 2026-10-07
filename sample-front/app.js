/* Launchpad 샘플 프런트. 정적 HTML + fetch로 /api 를 호출한다. 서버 규약은 README.md 참고.
 * 백엔드가 /api 에 응답하지 않으면 mock-api.js 의 예시 데이터로 대신 동작한다. */
(() => {
  'use strict';

  // ---------- 작은 DOM 도우미. 사용자 입력은 항상 textContent로만 넣는다 (XSS 방지) ----------
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === false || value == null) continue;
      if (key === 'class') node.className = value;
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  };
  const byId = (id) => document.getElementById(id);

  // ---------- API 호출 ----------
  let usingMock = location.protocol === 'file:';

  function showMockBanner() {
    if (byId('mock-banner')) return;
    const banner = el('div', { id: 'mock-banner', class: 'mock-banner', role: 'note' },
      '예시 데이터 모드: 백엔드(/api)가 없어서 이 브라우저 안의 가짜 데이터로 동작 중입니다. 실제 비밀번호는 입력하지 마세요.');
    document.body.prepend(banner);
  }

  function mockCall(path, method, body) {
    showMockBanner();
    return window.LaunchpadMock.handle(path, { method, body });
  }

  async function api(path, { method = 'GET', body } = {}) {
    if (!usingMock) {
      let res;
      try {
        res = await fetch('/api' + path, {
          method,
          headers: body ? { 'Content-Type': 'application/json' } : {},
          body: body ? JSON.stringify(body) : undefined,
          credentials: 'same-origin',
        });
      } catch (networkError) {
        res = null; // 서버에 닿지 않음
      }
      const isJson = res && (res.headers.get('content-type') || '').includes('application/json');
      if (res && (isJson || res.status === 204)) {
        const data = res.status === 204 ? null : await res.json().catch(() => null);
        if (!res.ok) {
          const error = new Error((data && data.error) || '요청을 처리하지 못했습니다.');
          error.status = res.status;
          throw error;
        }
        return data;
      }
      // JSON이 아닌 응답(정적 서버의 404 등)이거나 서버가 없으면 백엔드가 없는 것으로 본다
      usingMock = true;
    }
    return mockCall(path, method, body);
  }

  async function currentUser() {
    try {
      return await api('/me');
    } catch (err) {
      if (err.status === 401) return null;
      throw err;
    }
  }

  // ---------- 안내 문구 (페이지를 넘어가도 한 번 보여준다) ----------
  const NOTICE_KEY = 'launchpad-notice';
  function setNotice(message, kind = 'ok') {
    try { sessionStorage.setItem(NOTICE_KEY, JSON.stringify({ message, kind })); } catch (e) { /* 저장 불가 환경이면 생략 */ }
  }
  function flash(message, kind = 'ok') {
    const box = byId('flash');
    if (!box) return;
    box.replaceChildren(message
      ? el('p', { class: `flash flash-${kind === 'ok' ? 'ok' : 'err'}`, role: kind === 'ok' ? 'status' : 'alert' }, message)
      : '');
  }
  function showStoredNotice() {
    try {
      const raw = sessionStorage.getItem(NOTICE_KEY);
      if (!raw) return;
      sessionStorage.removeItem(NOTICE_KEY);
      const { message, kind } = JSON.parse(raw);
      flash(message, kind);
    } catch (e) { /* 무시 */ }
  }

  const pad = (n) => String(n).padStart(2, '0');
  function formatDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ---------- 공통: 상단 메뉴, 푸터 ----------
  async function logout() {
    try { await api('/logout', { method: 'POST' }); } catch (e) { /* 이미 로그아웃된 경우 */ }
    setNotice('로그아웃했습니다.');
    location.href = 'index.html';
  }

  function renderNav(user) {
    const nav = byId('nav');
    if (user) {
      nav.replaceChildren(
        el('span', { class: 'nav-user' },
          el('span', { class: 'avatar', 'aria-hidden': 'true' }, Array.from(user.name)[0] || ''),
          user.name),
        el('button', { class: 'btn btn-ghost btn-sm', type: 'button', onclick: logout }, '로그아웃'));
    } else {
      nav.replaceChildren(
        el('a', { class: 'btn btn-ghost btn-sm', href: 'login.html' }, '로그인'),
        el('a', { class: 'btn btn-primary btn-sm', href: 'signup.html' }, '가입하기'));
    }
  }

  async function renderHost() {
    try {
      const info = await api('/info');
      if (!info || !info.hostname) return;
      const host = byId('host');
      host.querySelector('code').textContent = info.hostname;
      host.hidden = false;
    } catch (e) { /* 선택 기능: 없으면 숨김 */ }
  }

  // ---------- 메인(보드) ----------
  function renderHeroCta(user) {
    byId('hero-cta').replaceChildren(...(user
      ? [el('a', { class: 'btn btn-primary', href: '#new-idea' }, '아이디어 올리기')]
      : [el('a', { class: 'btn btn-primary', href: 'signup.html' }, '가입하고 시작하기'),
         el('a', { class: 'btn btn-ghost', href: 'login.html' }, '로그인')]));
  }

  function renderStats(stats) {
    const item = (label, value) => el('div', {}, el('dt', {}, label), el('dd', {}, value));
    byId('stats').replaceChildren(item('참여자', stats.users), item('아이디어', stats.ideas), item('응원', stats.votes));
  }

  function renderComposer(user, onPosted) {
    const box = byId('composer');
    box.id = 'new-idea';
    if (!user) {
      box.replaceChildren(
        el('h2', {}, '당신의 아이디어는?'),
        el('p', { class: 'muted' }, '로그인하면 아이디어를 올리고 다른 사람의 아이디어에 응원할 수 있어요.'),
        el('a', { class: 'btn btn-primary btn-block', href: 'signup.html' }, '가입하기'),
        el('a', { class: 'btn btn-ghost btn-block', href: 'login.html' }, '로그인'));
      return;
    }
    const title = el('input', { id: 'title', name: 'title', type: 'text', maxlength: 80, required: true, placeholder: '예: 동네 소상공인을 위한 재고 알림 앱' });
    const description = el('textarea', { id: 'description', name: 'description', rows: 4, maxlength: 300, placeholder: '누구의 어떤 문제를 해결하나요?' });
    const submit = el('button', { class: 'btn btn-primary btn-block', type: 'submit' }, '올리기');
    const form = el('form', { class: 'form' },
      el('label', { for: 'title' }, '한 줄 소개'), title,
      el('label', { for: 'description' }, '설명 ', el('span', { class: 'muted' }, '(선택)')), description,
      submit);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      submit.disabled = true;
      try {
        await api('/ideas', { method: 'POST', body: { title: title.value, description: description.value } });
        title.value = '';
        description.value = '';
        flash('아이디어를 올렸습니다.');
        await onPosted();
      } catch (err) {
        flash(err.message, 'err');
      } finally {
        submit.disabled = false;
      }
    });
    box.replaceChildren(el('h2', {}, '새 아이디어'), form);
  }

  function renderIdeas(ideas, user, reload) {
    const list = byId('ideas');
    byId('empty').hidden = ideas.length > 0;
    list.replaceChildren(...ideas.map((idea) => {
      const button = el('button', {
        type: 'button',
        class: `vote${idea.voted ? ' is-voted' : ''}`,
        'aria-pressed': idea.voted ? 'true' : 'false',
        'aria-label': `${idea.title} 응원하기, 현재 ${idea.votes}명`,
      }, el('span', { class: 'vote-arrow', 'aria-hidden': 'true' }, '▲'), el('span', {}, idea.votes));
      button.addEventListener('click', async () => {
        if (!user) {
          setNotice('로그인이 필요합니다.', 'err');
          location.href = 'login.html';
          return;
        }
        button.disabled = true;
        try {
          await api(`/ideas/${idea.id}/vote`, { method: 'POST' });
          await reload();
        } catch (err) {
          flash(err.message, 'err');
          button.disabled = false;
        }
      });
      return el('li', { class: 'card idea', id: `idea-${idea.id}` },
        el('div', { class: 'vote-form' }, button),
        el('div', { class: 'idea-body' },
          el('h3', {}, idea.title),
          idea.description ? el('p', {}, idea.description) : null,
          el('p', { class: 'meta' }, `${idea.author} · ${formatDate(idea.created_at)}`)));
    }));
  }

  async function initBoard() {
    const user = await currentUser();
    renderNav(user);
    renderHeroCta(user);
    showStoredNotice();
    const load = async () => {
      const data = await api('/ideas');
      renderStats(data.stats);
      renderIdeas(data.ideas, user, load);
    };
    renderComposer(user, load);
    await load();
  }

  // ---------- 로그인 / 가입 ----------
  async function initAuth(mode) {
    const user = await currentUser();
    if (user) { location.replace('index.html'); return; }
    renderNav(null);
    showStoredNotice();
    const form = byId('auth-form');
    const errorBox = byId('auth-error');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      errorBox.hidden = true;
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        await api(`/${mode}`, { method: 'POST', body: Object.fromEntries(new FormData(form)) });
        setNotice(mode === 'signup' ? '가입을 환영합니다! 첫 아이디어를 올려 보세요.' : '로그인했습니다.');
        location.href = 'index.html';
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.hidden = false;
        button.disabled = false;
      }
    });
  }

  // ---------- 시작 ----------
  document.addEventListener('DOMContentLoaded', async () => {
    const page = document.body.dataset.page;
    try {
      if (page === 'board') await initBoard();
      else if (page === 'login' || page === 'signup') await initAuth(page);
    } catch (err) {
      flash('화면을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.', 'err');
      console.error(err);
    }
    renderHost();
  });
})();
