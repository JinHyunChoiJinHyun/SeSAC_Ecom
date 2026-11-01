'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const icons = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    inbox: '<path d="M4 4h16l2 11v5H2v-5L4 4z"/><path d="M2 15h6l2 3h4l2-3h6"/>',
    alert: '<path d="M10.3 4.5L2.7 18a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 4.5a2 2 0 00-3.4 0z"/><path d="M12 9v4m0 4h.01"/>',
    book: '<path d="M12 5C9 3 5 3 2 4v16c4-1 7-1 10 1 3-2 6-2 10-1V4c-3-1-7-1-10 1zm0 0v16"/>',
    refresh: '<path d="M20 7a9 9 0 00-15-2L2 8m0-5v5h5m-3 9a9 9 0 0015 2l3-3m0 5v-5h-5"/>',
    download: '<path d="M12 3v12m-5-5l5 5 5-5M4 16v5h16v-5"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
    route: '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="19" r="2"/><path d="M8 5h7a4 4 0 010 8H9a3 3 0 000 6h7"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M16 16l5 5"/>'
  };
  document.querySelectorAll('[data-icon]').forEach(el => {
    el.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[el.dataset.icon] || ''}</svg>`;
  });
  const labels = { completed: '분석 완료', failed: '실패·오류', running: '처리 중', unverified: '검증 필요', unfinished: '종료 미확인', unknown: '확인 불가' };
  const deliveryLabels = { success: '전송 완료', partial_success: '일부 실패', failed: '전송 실패', unknown: '미기록' };
  const viewLabels = { overview: ['운영 현황', 'OVERVIEW', '분석 요청의 흐름과 처리 결과를 확인하세요.'], requests: ['요청 내역', 'REQUESTS', '질문부터 최종 결과까지, 요청별 처리 기록을 살펴보세요.'], errors: ['오류 모니터링', 'ERROR MONITOR', '분석 실패와 오류 신호가 남은 요청을 확인하세요.'], guide: ['연결 안내', 'GETTING STARTED', '기존 프로젝트와 안전하게 연결하는 방법을 확인하세요.'] };
  const state = { view: 'overview', page: 1, pageSize: 12, data: null, loading: false, sequence: 0, selected: null, detailSequence: 0, controller: null, timer: null, previousFocus: null };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const number = n => new Intl.NumberFormat('ko-KR').format(n);
  const seconds = n => typeof n === 'number' && Number.isFinite(n) ? n.toFixed(1) : '—';
  const badge = status => `<span class="badge ${Object.hasOwn(labels, status) ? status : 'unknown'}">${esc(labels[status] || '확인 불가')}</span>`;
  const deliveryText = status => deliveryLabels[status] || '미기록';
  const sourceText = source => ({ log: '기존 로그', telemetry: '운영 기록' }[source] || '미기록');

  function params(includePage = true) {
    const p = new URLSearchParams({ q: $('search').value.trim(), status: state.view === 'errors' ? 'failed' : $('status-filter').value, start: $('start-date').value, end: $('end-date').value });
    if (includePage) { p.set('page', state.page); p.set('page_size', state.pageSize); }
    return p;
  }

  function toast(message) {
    $('toast').textContent = message; $('toast').hidden = false;
    clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2500);
  }

  async function fetchJSON(url, signal) {
    const response = await fetch(url, { signal, cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(response.status === 401 ? '관리자 인증이 필요합니다. 페이지를 다시 열어 로그인해 주세요.' : typeof payload.detail === 'string' ? payload.detail : '서버 응답을 확인할 수 없습니다.');
    }
    return response.json();
  }

  async function load(quiet = false) {
    if ($('start-date').value && $('end-date').value && $('start-date').value > $('end-date').value) {
      $('connection-error').hidden = false; $('connection-message').textContent = '시작 날짜가 종료 날짜보다 늦습니다.'; return;
    }
    const seq = ++state.sequence;
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    state.loading = true;
    $('refresh').disabled = true;
    if (!state.data && !quiet) $('request-body').innerHTML = '<tr class="loading"><td colspan="6">운영 기록을 불러오는 중입니다…</td></tr>';
    try {
      const data = await fetchJSON(`/api/requests?${params()}`, state.controller.signal);
      if (seq !== state.sequence) return;
      if (state.page > 1 && data.items.length === 0) { state.page = Math.max(1, Math.ceil(data.total / state.pageSize)); return load(); }
      state.data = data;
      render(data);
      $('connection-error').hidden = true;
      $('updated-at').textContent = `최근 갱신 ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
      if (quiet && state.selected && $('detail-dialog').open) showDetail(state.selected, true);
    } catch (error) {
      if (error.name === 'AbortError' || seq !== state.sequence) return;
      $('connection-error').hidden = false;
      $('connection-message').textContent = `${error.message}${state.data ? ' · 아래에는 마지막 조회 결과가 남아 있습니다.' : ''}`;
      if (!state.data) { $('request-body').innerHTML = ''; $('empty-state').hidden = false; $('empty-state').querySelector('h3').textContent = '연결을 확인해 주세요'; $('page-info').textContent = '조회 실패'; }
    } finally {
      if (seq === state.sequence) { state.loading = false; $('refresh').disabled = false; }
    }
  }

  function render(data) {
    const s = data.summary, counts = s.counts;
    $('mode-chip').textContent = 'PROJECT LOGS';
    $('side-mode').textContent = `프로젝트 기록 · 로그 ${data.log_files}개`;
    $('metric-total').innerHTML = `${number(s.total)}<small>건</small>`;
    $('metric-completed').innerHTML = `${number(counts.completed || 0)}<small>건</small>`;
    $('metric-failed').innerHTML = `${number(counts.failed || 0)}<small>건</small>`;
    $('metric-duration').innerHTML = `${seconds(s.average_duration)}<small>초</small>`;
    $('duration-note').textContent = `소요 시간 기록 ${number(s.duration_count)}건 기준`;
    $('completed-ratio').textContent = s.total ? `${Math.round((counts.completed || 0) / s.total * 100)}%` : '—';
    $('delivery-failed').textContent = `${s.delivery_failed}건`;
    $('nav-count').textContent = number(data.total);
    $('status-strip').innerHTML = `<span>처리 중 <b>${counts.running || 0}</b></span><span class="strip-divider"></span><span>검증 필요 <b>${counts.unverified || 0}</b></span><span>종료 미확인 <b>${counts.unfinished || 0}</b></span><span>확인 불가 <b>${counts.unknown || 0}</b></span><span>· 종료 기록만으로 분석 성공을 확정하지 않습니다.</span>`;
    drawTrend(s.timeline);
    const entries = Object.entries(s.agents).sort((a, b) => b[1] - a[1]).slice(0, 6), max = Math.max(1, ...entries.map(x => x[1]));
    $('agent-chart').innerHTML = entries.length ? entries.map(([name, value]) => `<div class="agent-row"><span>${esc(name)}</span><div class="agent-track"><span class="agent-fill" style="width:${value / max * 100}%"></span></div><b>${number(value)}</b></div>`).join('') : '<div class="chart-empty">실행 단계 기록 없음</div>';
    $('table-count').textContent = number(data.total);
    $('request-body').innerHTML = data.items.map(r => `<tr><td class="question-cell"><button class="question-button" data-request="${esc(r.id)}">${esc(r.question)}</button><span class="request-id">${esc(r.id)} · ${esc(r.channel)} · ${sourceText(r.origin)}</span></td><td>${badge(r.status)}</td><td><span class="delivery-${r.delivery_status === 'success' ? 'success' : ['failed', 'partial_success'].includes(r.delivery_status) ? 'failed' : 'unknown'}">${deliveryText(r.delivery_status)}</span></td><td>${r.started_at ? `<span>${esc(r.started_at.slice(5, 10).replace('-', '.'))}</span> <span>${esc(r.started_at.slice(11, 16))}</span>` : '미기록'}</td><td class="align-right">${seconds(r.duration)}${r.duration != null ? '초' : ''}</td><td><button class="row-open" data-request="${esc(r.id)}" aria-label="${esc(r.id)} 상세 보기">↗</button></td></tr>`).join('');
    $('empty-state').hidden = data.items.length !== 0;
    $('empty-state').querySelector('h3').textContent = '표시할 요청이 없습니다';
    $('page-info').textContent = data.total ? `전체 ${number(data.total)}건 중 ${number((state.page - 1) * state.pageSize + 1)}–${number(Math.min(state.page * state.pageSize, data.total))}건` : '0건';
    $('page-number').textContent = `${state.page} / ${Math.max(1, Math.ceil(data.total / state.pageSize))}`;
    $('prev-page').disabled = state.page <= 1;
    $('next-page').disabled = state.page * state.pageSize >= data.total;
    $('notices').replaceChildren(...data.notices.map(message => { const p = document.createElement('p'); p.textContent = message; return p; }));
    $('export').href = `/api/export.csv?${params(false)}`;
  }

  function drawTrend(days) {
    const container = $('trend-chart');
    if (!days.length) { container.innerHTML = '<div class="chart-empty">선택한 기간에 요청 기록이 없습니다.</div>'; return; }
    const w = Math.max(250, container.clientWidth - 26), h = 188, left = 33, right = 17, top = 16, bottom = 28;
    const max = Math.max(1, ...days.map(d => d.total)), ceiling = Math.max(2, Math.ceil(max / 4) * 4);
    const x = i => days.length === 1 ? (left + w - right) / 2 : left + i / (days.length - 1) * (w - left - right);
    const y = value => h - bottom - value / ceiling * (h - bottom - top);
    const line = key => days.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d[key])}`).join(' ');
    const ticks = Array.from({ length: 5 }, (_, i) => ceiling * i / 4);
    const step = Math.max(1, Math.ceil(days.length / (w < 350 ? 3 : 6)));
    const marks = days.map((d, i) => `<circle cx="${x(i)}" cy="${y(d.total)}" r="3" fill="#7d86ec"><title>${esc(d.date)}: 전체 ${d.total}건, 오류 ${d.failed}건</title></circle><circle cx="${x(i)}" cy="${y(d.failed)}" r="2.3" fill="#e3a0ad"/>`).join('');
    container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="일별 요청 수. ${esc(days.map(d => `${d.date} 전체 ${d.total}건 오류 ${d.failed}건`).join(', '))}">${ticks.map(v => `<line x1="${left}" y1="${y(v)}" x2="${w - right}" y2="${y(v)}" stroke="#edf0f6"/><text x="${left - 10}" y="${y(v) + 3}" text-anchor="end" fill="#adb6c7" font-size="9">${v}</text>`).join('')}<text x="${left - 10}" y="8" text-anchor="end" fill="#adb6c7" font-size="9">건</text><path d="${line('total')} L${x(days.length - 1)},${h - bottom} L${x(0)},${h - bottom}Z" fill="#7b83ed" opacity=".06"/><path d="${line('total')}" fill="none" stroke="#7b83ed" stroke-width="2"/><path d="${line('failed')}" fill="none" stroke="#e3a0ad" stroke-width="1.5"/>${marks}${days.map((d, i) => i % step === 0 || i === days.length - 1 ? `<text x="${x(i)}" y="${h - 6}" text-anchor="${i === 0 ? 'start' : i === days.length - 1 ? 'end' : 'middle'}" font-size="9" fill="#a3aec2">${esc(d.date.slice(5).replace('-', '.'))}</text>` : '').join('')}</svg>`;
  }

  function bytes(n) { return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`; }

  async function showDetail(id, quiet = false) {
    const seq = ++state.detailSequence;
    if (!quiet) {
      state.previousFocus = document.activeElement;
      state.selected = id;
      $('detail-title').textContent = '요청 상세';
      $('detail-content').innerHTML = '<div class="loading-block">요청 기록을 불러오는 중입니다…</div>';
      if (!$('detail-dialog').open) $('detail-dialog').showModal();
    }
    try {
      const r = await fetchJSON(`/api/requests/${encodeURIComponent(id)}`);
      if (seq !== state.detailSequence || !$('detail-dialog').open) return;
      $('detail-title').textContent = r.id;
      const files = r.files.map(f => {
        const url = `/api/files/${encodeURIComponent(r.id)}/${encodeURIComponent(f.id)}`;
        return `<div class="file-entry"><div><strong>${esc(f.name)}</strong><small>${esc(f.kind.toUpperCase())} · ${bytes(f.size)}</small></div><a href="${url}?download=true">다운로드 ↓</a></div>${['png', 'jpg', 'jpeg'].includes(f.kind) ? `<img class="chart-preview" src="${url}" alt="${esc(f.name)} 분석 차트" loading="lazy">` : ''}`;
      }).join('');
      const originMessage = r.origin === 'log' ? '기존 로그에 최종 답변 본문이 없습니다. 현재 로그만으로는 확인할 수 없습니다.' : r.status === 'running' ? '요청을 처리하고 있습니다. 자동 갱신을 켜면 상태를 다시 확인합니다.' : '저장된 최종 답변이 없습니다.';
      const oldOpen = quiet ? Array.from($('detail-content').querySelectorAll('details')).map(x => x.open) : [];
      $('detail-content').innerHTML = `<div class="detail-inner"><div class="detail-statuses">${badge(r.status)}<span class="badge">Slack · ${deliveryText(r.delivery_status)}</span><span class="badge">${sourceText(r.origin)}</span></div><p class="detail-question">${esc(r.question)}</p><div class="detail-meta"><span>접수 ${esc(r.started_at || '미기록')}</span><span>종료 ${esc(r.ended_at || '미기록')}</span><span>소요 ${seconds(r.duration)}${r.duration == null ? '' : '초'}</span><span>채널 ${esc(r.channel)}</span><span>처리 로그 ${esc(r.handler_status)}</span></div>${(r.warnings || []).map(w => `<p class="detail-warning">${esc(w)}</p>`).join('')}<section class="detail-section"><h3>최종 답변</h3><div class="answer-block ${r.answer ? '' : 'absent'}">${esc(r.answer || originMessage)}</div></section><section class="detail-section"><h3>실행 흐름</h3><div class="stage-list">${r.stages.length ? r.stages.map((s, i) => `${i ? '<span class="stage-arrow">→</span>' : ''}<span class="stage">${esc(s.name)}</span>`).join('') : '<p class="detail-empty">실행 단계 기록 없음</p>'}</div></section><section class="detail-section"><h3>생성 파일</h3>${files || `<p class="detail-empty">${'요청에 연결된 파일 기록이 없거나 파일이 존재하지 않습니다.'}</p>`}</section>${r.errors?.length ? `<section class="detail-section"><h3>오류 신호</h3>${r.errors.map(e => `<p class="error-line">${esc(e)}</p>`).join('')}</section>` : ''}<details><summary>Supervisor 판단 이유</summary><pre class="code-block">${esc(r.decisions?.join('\n\n') || '판단 이유 기록 없음')}</pre></details><details><summary>생성 SQL</summary><pre class="code-block">${esc(r.sql || 'SQL 기록 없음')}</pre></details><details><summary>상세 로그 · 최근 최대 2,000줄</summary><pre class="code-block">${esc(r.logs?.map(l => `${l.time} | ${l.level} | ${l.message}`).join('\n') || '로그 기록 없음')}</pre></details></div>`;
      if (quiet) $('detail-content').querySelectorAll('details').forEach((x, i) => { x.open = oldOpen[i]; });
    } catch (error) {
      if (seq === state.detailSequence && $('detail-dialog').open) $('detail-content').innerHTML = `<div class="loading-block">${esc(error.message)}<p>창을 닫고 요청을 다시 선택해 주세요.</p></div>`;
    }
  }

  function setView(view) {
    state.view = view; state.page = 1;
    const [title, eyebrow, subtitle] = viewLabels[view];
    $('page-title').textContent = title; $('breadcrumb-title').textContent = title; $('eyebrow').textContent = eyebrow; $('page-subtitle').textContent = subtitle;
    document.querySelectorAll('[data-view]').forEach(b => { const active = b.dataset.view === view; b.classList.toggle('active', active); if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    $('guide-view').hidden = view !== 'guide'; $('data-view').hidden = view === 'guide';
    $('charts-section').hidden = view !== 'overview'; $('metrics').hidden = view === 'requests'; $('status-strip').hidden = view === 'requests';
    $('status-filter').disabled = view === 'errors';
    $('table-title').firstChild.textContent = view === 'errors' ? '오류 요청 ' : view === 'requests' ? '요청 내역 ' : '최근 요청 ';
    $('export').hidden = view === 'guide';
    if (view !== 'guide') load();
  }

  function resetFilters() { $('search').value = ''; $('status-filter').value = 'all'; $('start-date').value = ''; $('end-date').value = ''; state.page = 1; load(); }
  let searchTimer;
  $('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.page = 1; load(); }, 250); });
  ['status-filter', 'start-date', 'end-date'].forEach(id => $(id).addEventListener('change', () => { state.page = 1; load(); }));
  $('date-reset').addEventListener('click', () => { $('start-date').value = ''; $('end-date').value = ''; state.page = 1; load(); });
  $('clear-filters').addEventListener('click', resetFilters);
  $('refresh').addEventListener('click', () => load()); $('retry').addEventListener('click', () => load());
  $('prev-page').addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } });
  $('next-page').addEventListener('click', () => { if (state.data && state.page * state.pageSize < state.data.total) { state.page++; load(); } });
  $('request-body').addEventListener('click', event => { const b = event.target.closest('[data-request]'); if (b) showDetail(b.dataset.request); });
  document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  $('close-detail').addEventListener('click', () => $('detail-dialog').close());
  $('detail-dialog').addEventListener('click', event => { if (event.target === $('detail-dialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
  $('detail-dialog').addEventListener('close', () => { state.selected = null; state.detailSequence++; state.previousFocus?.focus(); });
  $('auto-refresh').addEventListener('change', () => { clearInterval(state.timer); if ($('auto-refresh').checked) { state.timer = setInterval(() => { if (!document.hidden && state.view !== 'guide' && !state.loading) load(true); }, 10000); toast('화면이 열려 있는 동안 10초마다 갱신합니다.'); } });
  if (typeof ResizeObserver !== 'undefined') { let resizeTimer; new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.data && state.view === 'overview') drawTrend(state.data.summary.timeline); }, 100); }).observe($('trend-chart')); }
  load();
})();
