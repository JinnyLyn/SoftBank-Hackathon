/* MOCK: 백엔드가 없을 때만 쓰이는, 브라우저 안의 가짜 API다. 실제 서버가 아니다.
 * - 데이터는 이 브라우저의 localStorage에만 있고, 다른 사람이나 다른 브라우저와 공유되지 않는다.
 * - 서버가 /api 에 JSON으로 응답하면 app.js가 이 파일을 쓰지 않는다. 백엔드 연결이 끝나면 지워도 된다.
 * - 규약은 README.md 의 /api 표와 같다. 오류는 { status, message } 를 가진 Error 로 던진다. */
(() => {
  'use strict';

  const DB_KEY = 'launchpad-mock-db-v1';
  const ME_KEY = 'launchpad-mock-me-v1';
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  const fail = (status, message) => Object.assign(new Error(message), { status });

  // MOCK: 보안용 해시가 아니다. 가짜 계정에 쓰는 비밀번호 비교용일 뿐이다
  function hash(text) {
    let h = 0x811c9dc5;
    for (const ch of 'mock:' + text) {
      h ^= ch.codePointAt(0);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16);
  }

  function seed() {
    const minutesAgo = (m) => new Date(Date.now() - m * 60000).toISOString();
    return {
      nextUserId: 5,
      nextIdeaId: 6,
      // pw가 '-' 인 계정은 로그인할 수 없는 예시 작성자다
      users: [
        { id: 1, name: '박서연', email: 'seoyeon@example.com', pw: '-' },
        { id: 2, name: '이도윤', email: 'doyun@example.com', pw: '-' },
        { id: 3, name: '최민지', email: 'minji@example.com', pw: '-' },
        { id: 4, name: '정하늘', email: 'haneul@example.com', pw: '-' },
      ],
      ideas: [
        { id: 1, userId: 1, title: '1인 가구를 위한 주 1회 장보기 구독', description: '냉장고 재고를 기준으로 일주일 치 장보기 목록을 자동으로 만들어 새벽에 배송해 줘요.', createdAt: minutesAgo(300) },
        { id: 2, userId: 2, title: '동네 소상공인 재고 알림 앱', description: '품절 직전 상품을 사장님께 카카오톡으로 알려 주는 가벼운 도구예요.', createdAt: minutesAgo(240) },
        { id: 3, userId: 3, title: '예비 창업자 멘토 매칭', description: '업종과 단계가 같은 선배 창업자와 30분 커피챗을 이어 줘요.', createdAt: minutesAgo(180) },
        { id: 4, userId: 1, title: '반려동물 병원 예약 한 번에', description: '여러 병원의 빈 시간을 한 화면에서 비교하고 바로 예약해요.', createdAt: minutesAgo(120) },
        { id: 5, userId: 2, title: '스타트업 지원사업 알리미', description: '내 업종에 맞는 정부·지자체 지원사업을 마감 전에 알려 줘요.', createdAt: minutesAgo(60) },
      ],
      votes: [[3, 1], [3, 2], [3, 4], [2, 3], [2, 4], [5, 3], [5, 1], [1, 4]], // [아이디어 id, 사용자 id]
    };
  }

  function load() {
    try {
      const raw = localStorage.getItem(DB_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* 저장소를 못 쓰면 이번 페이지 동안만 유지 */ }
    return seed();
  }

  let db = load();
  const save = () => { try { localStorage.setItem(DB_KEY, JSON.stringify(db)); } catch (e) { /* 무시 */ } };
  const getMeId = () => { try { return Number(localStorage.getItem(ME_KEY)) || 0; } catch (e) { return 0; } };
  const setMeId = (id) => { try { id ? localStorage.setItem(ME_KEY, String(id)) : localStorage.removeItem(ME_KEY); } catch (e) { /* 무시 */ } };
  const publicUser = (u) => ({ id: u.id, name: u.name, email: u.email });
  const me = () => db.users.find((u) => u.id === getMeId()) || null;
  const requireMe = () => me() || (() => { throw fail(401, '로그인이 필요합니다.'); })();

  function ideaView(idea, meId) {
    const votes = db.votes.filter(([ideaId]) => ideaId === idea.id).length;
    return {
      id: idea.id,
      title: idea.title,
      description: idea.description,
      author: (db.users.find((u) => u.id === idea.userId) || { name: '알 수 없음' }).name,
      votes,
      voted: db.votes.some(([ideaId, userId]) => ideaId === idea.id && userId === meId),
      created_at: idea.createdAt,
    };
  }

  const routes = {
    'GET /me': () => publicUser(requireMe()),

    'GET /info': () => ({ hostname: 'mock-browser' }),

    'POST /signup': (body = {}) => {
      const name = String(body.name || '').trim();
      const email = String(body.email || '').trim().toLowerCase();
      const password = String(body.password || '');
      if (name.length < 1 || name.length > 30) throw fail(400, '이름은 1~30자로 입력해 주세요.');
      if (email.length > 190 || !EMAIL_RE.test(email)) throw fail(400, '올바른 이메일 주소를 입력해 주세요.');
      if (password.length < 8 || password.length > 128) throw fail(400, '비밀번호는 8자 이상 128자 이하로 입력해 주세요.');
      if (db.users.some((u) => u.email === email)) throw fail(409, '이미 가입된 이메일입니다.');
      const user = { id: db.nextUserId++, name, email, pw: hash(password) };
      db.users.push(user);
      save();
      setMeId(user.id);
      return publicUser(user);
    },

    'POST /login': (body = {}) => {
      const email = String(body.email || '').trim().toLowerCase();
      const user = db.users.find((u) => u.email === email);
      // 이메일 존재 여부가 드러나지 않도록 같은 문구를 쓴다
      if (!user || user.pw !== hash(String(body.password || ''))) throw fail(401, '이메일 또는 비밀번호가 올바르지 않습니다.');
      setMeId(user.id);
      return publicUser(user);
    },

    'POST /logout': () => { setMeId(0); return null; },

    'GET /ideas': () => {
      const meId = getMeId();
      const ideas = db.ideas.map((i) => ideaView(i, meId))
        .sort((a, b) => b.votes - a.votes || b.id - a.id)
        .slice(0, 30);
      return { stats: { users: db.users.length, ideas: db.ideas.length, votes: db.votes.length }, ideas };
    },

    'POST /ideas': (body = {}) => {
      const user = requireMe();
      const title = String(body.title || '').trim();
      const description = String(body.description || '').trim();
      if (title.length < 1 || title.length > 80 || description.length > 300) {
        throw fail(400, '제목은 1~80자, 설명은 300자 이하로 입력해 주세요.');
      }
      const idea = { id: db.nextIdeaId++, userId: user.id, title, description, createdAt: new Date().toISOString() };
      db.ideas.push(idea);
      save();
      return ideaView(idea, user.id);
    },
  };

  function handle(path, { method = 'GET', body } = {}) {
    const voteMatch = path.match(/^\/ideas\/(\d+)\/vote$/);
    if (method === 'POST' && voteMatch) {
      const user = requireMe();
      const ideaId = Number(voteMatch[1]);
      if (!db.ideas.some((i) => i.id === ideaId)) throw fail(404, '존재하지 않는 아이디어입니다.');
      const index = db.votes.findIndex(([i, u]) => i === ideaId && u === user.id);
      if (index >= 0) db.votes.splice(index, 1); else db.votes.push([ideaId, user.id]);
      save();
      return { voted: index < 0, votes: db.votes.filter(([i]) => i === ideaId).length };
    }
    const route = routes[`${method} ${path}`];
    if (!route) throw fail(404, '지원하지 않는 요청입니다.');
    return route(body);
  }

  window.LaunchpadMock = { handle: (path, options) => Promise.resolve().then(() => handle(path, options)) };
})();
