// 관리자 화면의 기록 조회·검색·상세 표시·자동 갱신, 모델 설정 조회·변경, 구매 예측 모델 재학습과 외부 데이터 수집 관리를 담당합니다.
'use strict';
(() => {
  // =========================================================
  // 공통 DOM 접근과 화면 아이콘 정의
  // =========================================================
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
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M16 16l5 5"/>',
    model: '<path d="M4 19V5m0 14h16"/><path d="M7 15l4-5 3 3 5-7"/><circle cx="19" cy="6" r="1.5"/>',
    database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'
  };
  document.querySelectorAll('[data-icon]').forEach(el => {
    el.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[el.dataset.icon] || ''}</svg>`;
  });
  // =========================================================
  // 표시 문구, 조회 상태와 HTML 문자열 보호
  // =========================================================
  const labels = { completed: '분석 완료', failed: '실패·오류', running: '처리 중', unverified: '검증 필요', unfinished: '종료 미확인', unknown: '확인 불가' };
  const deliveryLabels = { success: '전송 완료', partial_success: '일부 실패', failed: '전송 실패', unknown: '미기록' };
  const viewLabels = { overview: ['운영 현황', '요청 처리 결과와 에이전트 실행, 데이터·모델 상태를 한눈에 확인하세요.'], requests: ['요청 내역', '질문부터 최종 답변까지, 요청별 처리 기록을 살펴보세요.'], errors: ['오류 모니터링', '실패하거나 오류가 남은 요청을 확인하세요.'], guide: ['연결 안내', '대시보드 실행 방법과 기록을 읽는 방식을 확인하세요.'], models: ['모델 관리', '대화에 쓰는 LLM과 고객 타깃 선정에 쓰는 구매 예측 모델을 관리하세요.'], external: ['외부 데이터', '네이버·유튜브·무신사 수집 상태와 최신 데이터를 확인하고, 필요하면 직접 수집하세요.'] };
  // 요청 기록을 쓰지 않는 화면
  const toolViews = ['guide', 'models', 'external'];
  // 실행 흐름 중 에이전트로 집계할 이름 (그 밖의 '입력 대기'·'Slack 전달' 같은 단계는 제외)
  const AGENTS = ['총괄 에이전트', '시장 분석', '상품 분석', '고객 타깃 선정', '콘텐츠 생성', '검증', '결과 정리', '캠페인 실행', '일반 분석'];
  // 질문 앞의 Slack 멘션을 지우고, 버튼·메일·파일 요청은 유형 표시로 바꿉니다.
  function questionParts(raw) {
    let text = String(raw ?? '').replace(/<@[A-Z0-9]+>/g, '').trim();
    const kinds = [['[메일 버튼]', '메일', 'mail'], ['[파일 첨부]', '파일', 'file'], ['[버튼]', '버튼', '']];
    for (const [prefix, label, cls] of kinds) {
      if (text.startsWith(prefix)) return { tag: label, cls, text: text.slice(prefix.length).trim() || label };
    }
    return { tag: '', cls: '', text: text || '질문 기록 없음' };
  }
  const questionHTML = raw => { const q = questionParts(raw); return `${q.tag ? `<span class="q-tag ${q.cls}">${esc(q.tag)}</span>` : ''}${esc(q.text)}`; };
  const state = { view: 'overview', page: 1, pageSize: 12, data: null, loading: false, sequence: 0, selected: null, detailSequence: 0, controller: null, timer: null, previousFocus: null, period: '30', dateContext: null, periodTimer: null };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const number = n => new Intl.NumberFormat('ko-KR').format(n);
  const seconds = n => typeof n === 'number' && Number.isFinite(n) ? n.toFixed(1) : '—';
  const badge = status => `<span class="badge ${Object.hasOwn(labels, status) ? status : 'unknown'}">${esc(labels[status] || '확인 불가')}</span>`;
  const deliveryText = status => deliveryLabels[status] || '미기록';

  // =========================================================
  // 현재 화면과 입력값을 API 검색 조건으로 변환
  // =========================================================
  function params(includePage = true) {
    const p = new URLSearchParams({ q: $('search').value.trim(), status: state.view === 'errors' ? 'failed' : $('status-filter').value, start: $('start-date').value, end: $('end-date').value });
    if (includePage) { p.set('page', state.page); p.set('page_size', state.pageSize); }
    return p;
  }

  // 상대 기간은 서버의 달력 날짜로 계산해 브라우저 시간대 차이를 배제합니다.
  // 직접 입력(custom)은 날짜가 바뀌거나 화면을 이동해도 그대로 유지합니다.
  function updatePeriodButtons() {
    document.querySelectorAll('[data-period]').forEach(button => {
      const selected = button.dataset.period === state.period;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
  }

  function applyPeriodDates() {
    if (state.period === 'custom') return;
    if (state.period === 'all') {
      $('start-date').value = ''; $('end-date').value = ''; return;
    }
    if (!state.dateContext) return;
    const today = state.dateContext.today;
    const start = new Date(`${today}T00:00:00Z`);
    start.setUTCDate(start.getUTCDate() - ({ today: 0, '7': 6, '30': 29 }[state.period]));
    $('start-date').value = start.toISOString().slice(0, 10);
    $('end-date').value = today;
  }

  function disableExport() {
    $('export').setAttribute('aria-disabled', 'true');
    $('export').removeAttribute('href');
  }

  function validDateRange() {
    const valid = !$('start-date').value || !$('end-date').value || $('start-date').value <= $('end-date').value;
    $('start-date').setCustomValidity(valid ? '' : '시작일이 종료일보다 늦습니다.');
    if (!valid) {
      $('connection-error').hidden = false;
      $('connection-message').textContent = '시작일이 종료일보다 늦습니다. 조회 기간을 확인해 주세요.';
    }
    return valid;
  }

  function selectPeriod(period) {
    state.period = period; state.page = 1;
    updatePeriodButtons(); applyPeriodDates();
    return load();
  }

  // =========================================================
  // 짧은 작업 안내 표시
  // =========================================================
  function toast(message) {
    $('toast').textContent = message; $('toast').hidden = false;
    clearTimeout(state.toastTimer); state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2500);
  }

  // =========================================================
  // JSON 조회와 HTTP 오류 안내
  // =========================================================
  async function fetchJSON(url, signal) {
    const response = await fetch(url, { signal, cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(response.status === 401 ? '관리자 인증이 필요합니다. 페이지를 다시 열어 로그인해 주세요.' : typeof payload.detail === 'string' ? payload.detail : '서버 응답을 확인할 수 없습니다.');
    }
    return response.json();
  }

  // =========================================================
  // 인증된 모델 설정 조회와 적용. 키는 서버에만 보관합니다.
  // =========================================================
  let modelOptions = [], modelBusy = false;
  function renderModelSettings(data) {
    modelOptions = data.options;
    // 설정 파일이 잘못되면 current가 없습니다. 이때도 모델을 다시 골라 적용할 수 있게 둡니다.
    const current = data.current;
    const currentOption = current && modelOptions.find(item => item.provider === current.provider && item.model === current.model);
    $('llm-current').textContent = current ? `${currentOption?.label || current.provider} · ${current.model}` : '설정 오류 · 모델을 다시 골라 적용해 주세요';
    $('llm-choice').replaceChildren(...modelOptions.map(item => {
      const option = document.createElement('option');
      option.value = item.provider;
      option.textContent = `${item.label} · ${item.model}${item.available ? '' : ' (API 키 미설정)'}`;
      option.selected = !!current && item.provider === current.provider;
      return option;
    }));
    $('llm-choice').disabled = false;
    $('llm-apply').disabled = false;
  }
  async function loadModelSettings() {
    try {
      const data = await fetchJSON('/api/model-settings');
      renderModelSettings(data);
      $('llm-feedback').textContent = data.warning || '';
    } catch (error) {
      $('llm-current').textContent = '설정 확인 필요';
      $('llm-feedback').textContent = error.message;
    }
  }
  $('llm-apply').addEventListener('click', async () => {
    if (modelBusy) return;
    const option = modelOptions.find(item => item.provider === $('llm-choice').value);
    if (!option) return;
    modelBusy = true;
    $('llm-apply').disabled = true;
    $('llm-feedback').textContent = '모델 설정을 적용하고 있습니다…';
    try {
      const response = await fetch('/api/model-settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: option.provider, model: option.model }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '모델 설정을 적용하지 못했습니다.');
      renderModelSettings(data);
      $('llm-feedback').textContent = '저장했습니다. 이후 새로 처리를 시작하는 요청부터 적용됩니다.';
    } catch (error) {
      $('llm-feedback').textContent = `적용 실패: ${error.message}`;
    } finally {
      modelBusy = false;
      $('llm-apply').disabled = false;
    }
  });

  // =========================================================
  // 이전 조회 취소, 최신 응답 확인과 목록 갱신
  // =========================================================
  async function load(quiet = false) {
    const seq = ++state.sequence;
    if (state.controller) state.controller.abort();
    disableExport();
    if (state.period === 'custom' && !validDateRange()) { state.loading = false; $('refresh').disabled = false; return false; }
    state.controller = new AbortController();
    state.loading = true;
    $('refresh').disabled = true;
    if (!state.data && !quiet) $('request-body').innerHTML = '<tr class="loading"><td colspan="6">운영 기록을 불러오는 중입니다…</td></tr>';
    try {
      // 로그를 기록하는 서버 기준의 오늘을 사용하며, 상대 기간은 매 조회 시 재계산합니다.
      const context = await fetchJSON('/api/date-context', state.controller.signal);
      if (seq !== state.sequence) return false;
      state.dateContext = context;
      applyPeriodDates();
      validDateRange();
      $('date-context').textContent = `운영 로그의 요청 접수일 기준 · ${context.timezone} · 종료일 포함`;
      clearTimeout(state.periodTimer);
      if (!['custom', 'all'].includes(state.period)) state.periodTimer = setTimeout(() => load(true), context.rollover_ms);
      const query = params();
      const data = await fetchJSON(`/api/requests?${query}`, state.controller.signal);
      if (seq !== state.sequence) return false;
      if (state.page > 1 && data.items.length === 0) { state.page = Math.max(1, Math.ceil(data.total / state.pageSize)); return load(); }
      state.data = data;
      render(data, query);
      if (state.view === 'overview') loadOverviewCards();
      $('connection-error').hidden = true;
      $('updated-at').textContent = `최근 갱신 ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
      if (quiet && state.selected && $('detail-dialog').open) showDetail(state.selected, true);
      return true;
    } catch (error) {
      if (error.name === 'AbortError' || seq !== state.sequence) return false;
      $('connection-error').hidden = false;
      $('connection-message').textContent = `${error.message}${state.data ? ' · 아래에는 마지막 조회 결과가 남아 있습니다.' : ''}`;
      if (!state.data) { $('request-body').innerHTML = ''; $('empty-state').hidden = false; $('empty-state').querySelector('h3').textContent = '연결을 확인해 주세요'; $('page-info').textContent = '조회 실패'; }
      return false;
    } finally {
      if (seq === state.sequence) { state.loading = false; $('refresh').disabled = false; }
    }
  }

  // =========================================================
  // 요약 지표, 요청 목록, 페이지와 CSV 링크 렌더링
  // =========================================================
  function render(data, query) {
    const s = data.summary, counts = s.counts;
    $('side-mode').textContent = `운영 로그 ${data.log_files}개 연결됨`;
    $('metric-total').innerHTML = `${number(s.total)}<small>건</small>`;
    $('metric-completed').innerHTML = `${number(counts.completed || 0)}<small>건</small>`;
    $('metric-failed').innerHTML = `${number(counts.failed || 0)}<small>건</small>`;
    $('metric-duration').innerHTML = `${seconds(s.average_duration)}<small>초</small>`;
    $('duration-note').textContent = `소요 시간 기록 ${number(s.duration_count)}건 기준`;
    $('completed-ratio').textContent = s.total ? `${Math.round((counts.completed || 0) / s.total * 100)}%` : '—';
    $('delivery-failed').textContent = `${s.delivery_failed}건`;
    const pill = (label, n) => `<span class="pill">${esc(label)} <b>${number(n || 0)}</b></span>`;
    $('status-strip').innerHTML = `${pill('처리 중', counts.running)}${pill('검증 필요', counts.unverified)}${pill('종료 미확인', counts.unfinished)}${pill('확인 불가', counts.unknown)}<span class="note">종료 기록만으로 분석 성공을 확정하지 않습니다.</span>`;
    drawTrend(s.timeline);
    drawAgents(s.agents);
    $('table-count').textContent = number(data.total);
    $('request-body').innerHTML = data.items.map(r => {
      // Slack 전달은 실패·일부 실패만 강조하고, 나머지는 흐리게 표시합니다.
      const bad = ['failed', 'partial_success'].includes(r.delivery_status);
      const when = r.started_at ? `${r.started_at.slice(5, 10).replace('-', '.')} ${r.started_at.slice(11, 16)}` : '미기록';
      return `<tr><td class="question-cell"><div class="question-line"><button class="question-button" data-request="${esc(r.id)}" title="${esc(questionParts(r.question).text)}">${questionHTML(r.question)}</button></div><span class="request-id">${esc(r.id)}${r.llm_model ? ` · ${esc(r.llm_model)}` : ''}</span></td><td>${badge(r.status)}</td><td><span class="${bad ? 'delivery-failed' : 'delivery-muted'}">${deliveryText(r.delivery_status)}</span></td><td>${esc(when)}</td><td class="align-right">${seconds(r.duration)}${r.duration != null ? '초' : ''}</td><td><button class="row-open" data-request="${esc(r.id)}" aria-label="${esc(r.id)} 상세 보기">↗</button></td></tr>`;
    }).join('');
    $('empty-state').hidden = data.items.length !== 0;
    $('empty-state').querySelector('h3').textContent = '표시할 요청이 없습니다';
    $('page-info').textContent = data.total ? `전체 ${number(data.total)}건 중 ${number((state.page - 1) * state.pageSize + 1)}–${number(Math.min(state.page * state.pageSize, data.total))}건` : '0건';
    $('page-number').textContent = `${state.page} / ${Math.max(1, Math.ceil(data.total / state.pageSize))}`;
    $('prev-page').disabled = state.page <= 1;
    $('next-page').disabled = state.page * state.pageSize >= data.total;
    $('notices').replaceChildren(...data.notices.map(message => { const p = document.createElement('p'); p.textContent = message; return p; }));
    // 응답을 받은 조회 조건을 그대로 사용해 통계·목록과 CSV의 기간을 맞춥니다.
    const exportQuery = new URLSearchParams(query);
    exportQuery.delete('page'); exportQuery.delete('page_size');
    $('export').href = `/api/export.csv?${exportQuery}`;
    $('export').removeAttribute('aria-disabled');
  }

  // =========================================================
  // 일별 요청·실패 건수를 SVG 차트로 표현
  // =========================================================
  // 차트 공통 색: 기본 계열은 브랜드 그린, 실패는 상태 레드(아이콘·범례와 함께 씀)
  const CHART = { line: '#2e9b63', bad: '#c8322a', grid: '#e3ebe6', axis: '#56655d' };

  // 요청이 없는 날을 0건으로 채워 날짜 간격이 실제와 같게 보이도록 합니다.
  function fillDays(days) {
    if (!days.length) return days;
    const start = $('start-date').value || days[0].date, end = $('end-date').value || days[days.length - 1].date;
    const byDate = Object.fromEntries(days.map(d => [d.date, d]));
    const out = [], cursor = new Date(`${start}T00:00:00Z`), last = new Date(`${end}T00:00:00Z`);
    for (let i = 0; cursor <= last && i < 800; i++) {
      const key = cursor.toISOString().slice(0, 10);
      out.push(byDate[key] || { date: key, total: 0, failed: 0 });
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    return out.length ? out : days;
  }

  // 마우스 위치의 가장 가까운 날짜를 툴팁으로 보여 줍니다(차트마다 하나).
  function attachHover(container, points, x, text) {
    const svg = container.querySelector('svg'); if (!svg) return;
    let tip = container.querySelector('.chart-tip');
    if (!tip) { tip = document.createElement('div'); tip.className = 'chart-tip'; tip.hidden = true; container.appendChild(tip); }
    const guide = svg.querySelector('.hover-guide');
    svg.addEventListener('pointermove', event => {
      const box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal, px = (event.clientX - box.left) * vb.width / box.width;
      let best = 0; points.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
      if (guide) { guide.setAttribute('x1', x(best)); guide.setAttribute('x2', x(best)); guide.style.display = ''; }
      tip.innerHTML = text(points[best]); tip.hidden = false;
      tip.style.left = `${x(best) * box.width / vb.width + svg.getBoundingClientRect().left - container.getBoundingClientRect().left}px`;
      tip.style.top = `${event.clientY - container.getBoundingClientRect().top}px`;
    });
    svg.addEventListener('pointerleave', () => { tip.hidden = true; if (guide) guide.style.display = 'none'; });
  }

  function drawTrend(raw) {
    const container = $('trend-chart');
    if (!raw.length) { container.innerHTML = '<div class="chart-empty">선택한 기간에 요청 기록이 없습니다.</div>'; return; }
    const days = fillDays(raw);
    const w = Math.max(260, container.clientWidth - 32), h = 210, left = 34, right = 12, top = 12, bottom = 26;
    const max = Math.max(1, ...days.map(d => d.total)), ceiling = Math.max(4, Math.ceil(max / 4) * 4);
    const x = i => days.length === 1 ? (left + w - right) / 2 : left + i / (days.length - 1) * (w - left - right);
    const y = value => h - bottom - value / ceiling * (h - bottom - top);
    const line = key => days.map((d, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(' ');
    const ticks = Array.from({ length: 5 }, (_, i) => ceiling * i / 4);
    const step = Math.max(1, Math.ceil(days.length / (w < 420 ? 4 : 7)));
    // 요청이 있었던 날만 점을 찍습니다(0건 날은 선만).
    const dots = days.map((d, i) => d.total ? `<circle cx="${x(i)}" cy="${y(d.total)}" r="4" fill="${CHART.line}" stroke="#fff" stroke-width="2"/>` : '').join('')
      + days.map((d, i) => d.failed ? `<circle cx="${x(i)}" cy="${y(d.failed)}" r="4" fill="${CHART.bad}" stroke="#fff" stroke-width="2"/>` : '').join('');
    container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="일별 요청 수. ${esc(raw.map(d => `${d.date} 전체 ${d.total}건 실패 ${d.failed}건`).join(', '))}">`
      + ticks.map(v => `<line x1="${left}" y1="${y(v)}" x2="${w - right}" y2="${y(v)}" stroke="${CHART.grid}"/><text x="${left - 8}" y="${y(v) + 4}" text-anchor="end" fill="${CHART.axis}" font-size="11">${v}</text>`).join('')
      + `<line class="hover-guide" x1="0" y1="${top}" x2="0" y2="${h - bottom}" stroke="#9fb6a9" stroke-dasharray="3 3" style="display:none"/>`
      + `<path d="${line('total')}" fill="none" stroke="${CHART.line}" stroke-width="2" stroke-linejoin="round"/><path d="${line('failed')}" fill="none" stroke="${CHART.bad}" stroke-width="2" stroke-linejoin="round"/>${dots}`
      + days.map((d, i) => (i % step === 0 && days.length - 1 - i >= step / 2) || i === days.length - 1 ? `<text x="${x(i)}" y="${h - 6}" text-anchor="${days.length === 1 ? 'middle' : i === 0 ? 'start' : i === days.length - 1 ? 'end' : 'middle'}" font-size="11" fill="${CHART.axis}">${esc(d.date.slice(5).replace('-', '.'))}</text>` : '').join('')
      + '</svg>';
    attachHover(container, days, x, d => `<b>${esc(d.date)}</b><br>전체 ${number(d.total)}건 · 실패 ${number(d.failed)}건`);
  }

  // 현재 구조의 에이전트만 집계합니다(예전 Supervisor 구조의 단계와 'Slack 전달' 같은 단계는 제외).
  function drawAgents(agents) {
    const totals = {};
    Object.entries(agents || {}).forEach(([name, n]) => {
      const base = name.replace(/ \(실패\)$/, '');
      if (AGENTS.includes(base)) totals[base] = (totals[base] || 0) + n;
    });
    const rows = Object.entries(totals).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...rows.map(r => r[1]));
    $('agent-chart').innerHTML = rows.length
      ? rows.map(([name, value]) => `<div class="agent-row" title="${esc(name)} ${number(value)}회"><span>${esc(name)}</span><div class="agent-track"><span class="agent-fill" style="width:${value / max * 100}%"></span></div><b>${number(value)}</b></div>`).join('')
      : '<div class="chart-empty">기간 안에 기록된 에이전트 실행이 없습니다.</div>';
  }

  // =========================================================
  // 운영 현황 요약 카드: 최근 오류, 외부 데이터, 사용 중인 모델
  // =========================================================
  async function loadOverviewCards() {
    const [errors, external, llm, retrain] = await Promise.allSettled([
      fetchJSON(`/api/requests?${new URLSearchParams({ status: 'failed', start: $('start-date').value, end: $('end-date').value, page: 1, page_size: 5 })}`),
      fetchJSON('/api/external/status'), fetchJSON('/api/model-settings'), fetchJSON('/api/retrain/status')]);
    if (state.view !== 'overview') return;
    if (errors.status === 'fulfilled') {
      const items = errors.value.items;
      $('ov-errors-card').classList.toggle('highlight', items.length > 0);
      $('ov-errors').innerHTML = items.length
        ? items.map(r => `<li><div class="ov-main"><button data-request="${esc(r.id)}">${esc(questionParts(r.question).text)}</button><small>${esc((r.started_at || '').slice(5, 16).replace('-', '.'))} · ${esc(r.id)}</small></div>${badge(r.status)}</li>`).join('')
        : '<li class="ov-empty">선택한 기간에 실패한 요청이 없습니다.</li>';
    } else $('ov-errors').innerHTML = `<li class="ov-empty">불러오지 못했습니다: ${esc(errors.reason.message)}</li>`;
    if (external.status === 'fulfilled') {
      $('ov-external').innerHTML = external.value.collectors.map(c => {
        const d = c.data, fresh = c.running ? ['실행 중', 'running'] : !d.exists ? ['없음', 'failed'] : c.stale ? ['오래됨', 'unverified'] : ['최신', 'completed'];
        return `<li><div class="ov-main"><span>${esc(c.title)}</span><small>${d.collected ? `${esc(d.collected)} · ${c.days_old === 0 ? '오늘' : `${c.days_old}일 전`}` : '데이터 없음'}</small></div><span class="badge ${fresh[1]}">${esc(fresh[0])}</span></li>`;
      }).join('');
    } else $('ov-external').innerHTML = `<li class="ov-empty">불러오지 못했습니다: ${esc(external.reason.message)}</li>`;
    const cur = llm.status === 'fulfilled' ? llm.value.current : null;
    const label = cur ? `${(llm.value.options.find(o => o.provider === cur.provider) || {}).label || cur.provider} · ${cur.model}` : '설정 확인 필요';
    const serving = retrain.status === 'fulfilled' ? retrain.value.serving_model : null;
    $('ov-model').innerHTML = `<span class="muted">LLM</span><strong>${esc(label)}</strong><span class="muted" style="margin-top:6px">구매 예측 모델</span><strong>${serving ? `${esc(serving.as_of)} 기준` : '없음'}</strong>${serving ? `<small class="muted">${esc(serving.run_id)} · 조합 ${serving.combos != null ? number(serving.combos) : '—'}개</small>` : ''}`;
  }

  // =========================================================
  // 첨부 파일 크기를 KB 또는 MB로 표시
  // =========================================================
  function bytes(n) { return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`; }

  // =========================================================
  // 요청 상세와 첨부 미리보기·로그 표시
  // =========================================================
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
      const originMessage = r.status === 'running' ? '요청을 처리하고 있습니다. 자동 갱신을 켜면 상태를 다시 확인합니다.' : '이 요청의 답변은 로그에 기록되지 않았습니다. 답변 기록 기능을 넣기 전의 요청이거나 답변 없이 끝난 요청입니다.';
      const oldOpen = quiet ? Array.from($('detail-content').querySelectorAll('details')).map(x => x.open) : [];
      const stageClass = name => /\(실패\)$|^중단/.test(name) ? ' bad' : /^입력 대기/.test(name) ? ' wait' : '';
      const meta = [['접수', r.started_at || '미기록'], ['종료', r.ended_at || '미기록'], ['소요', r.duration == null ? '—' : `${seconds(r.duration)}초`],
        ['Slack 전달', deliveryText(r.delivery_status)], ['LLM', r.llm_model ? `${r.llm_provider || ''} · ${r.llm_model}` : '미기록'], ['채널', r.channel]];
      const q = questionParts(r.question);
      $('detail-content').innerHTML = `<div class="detail-inner">
        <div class="detail-statuses">${badge(r.status)}${q.tag ? `<span class="q-tag ${q.cls}">${esc(q.tag)}</span>` : ''}</div>
        <p class="detail-question">${esc(q.text)}</p>
        <dl class="detail-meta">${meta.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
        ${(r.warnings || []).map(w => `<p class="detail-warning">${esc(w)}</p>`).join('')}
        <section class="detail-section"><h3>최종 답변</h3><div class="answer-block ${r.answer ? '' : 'absent'}">${esc(r.answer || originMessage)}</div></section>
        <section class="detail-section"><h3>실행 흐름</h3><div class="stage-list">${r.stages.length ? r.stages.map((s, i) => `${i ? '<span class="stage-arrow">→</span>' : ''}<span class="stage${stageClass(s.name)}">${esc(s.name)}</span>`).join('') : '<p class="detail-empty">실행 단계 기록 없음</p>'}</div></section>
        <section class="detail-section"><h3>판단 이유</h3>${r.decisions?.length ? r.decisions.map(d => `<div class="decision">${esc(d)}</div>`).join('') : '<p class="detail-empty">판단 이유 기록 없음</p>'}</section>
        <section class="detail-section"><h3>생성 파일</h3>${files || '<p class="detail-empty">이 요청에서 만든 파일이 없습니다.</p>'}</section>
        ${r.errors?.length ? `<section class="detail-section"><h3>오류 신호 <span class="count-chip">${r.errors.length}</span></h3>${r.errors.map(e => `<p class="error-line">${esc(e)}</p>`).join('')}</section>` : ''}
        ${r.sql ? `<details><summary>생성 SQL</summary><pre class="code-block">${esc(r.sql)}</pre></details>` : ''}
        <details><summary>상세 로그 · 최근 최대 2,000줄</summary><pre class="code-block">${esc(r.logs?.map(l => `${l.time} | ${l.level} | ${l.message}`).join('\n') || '로그 기록 없음')}</pre></details>
      </div>`;
      if (quiet) $('detail-content').querySelectorAll('details').forEach((x, i) => { x.open = oldOpen[i]; });
    } catch (error) {
      if (seq === state.detailSequence && $('detail-dialog').open) $('detail-content').innerHTML = `<div class="loading-block">${esc(error.message)}<p>창을 닫고 요청을 다시 선택해 주세요.</p></div>`;
    }
  }

  // =========================================================
  // 메뉴에 따른 제목·필터·표시 영역 전환
  // =========================================================
  function setView(view) {
    state.view = view; state.page = 1;
    const [title, subtitle] = viewLabels[view];
    $('page-title').textContent = title; $('breadcrumb-title').textContent = title; $('page-subtitle').textContent = subtitle;
    document.title = `${title} · CART_SIGNAL`;
    document.querySelectorAll('.nav-item[data-view]').forEach(b => { const active = b.dataset.view === view; b.classList.toggle('active', active); if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    const dataView = !toolViews.includes(view), overview = view === 'overview';
    $('guide-view').hidden = view !== 'guide'; $('models-view').hidden = view !== 'models'; $('external-view').hidden = view !== 'external'; $('data-view').hidden = !dataView;
    // 운영 현황은 요약만, 요청 내역·오류 모니터링은 목록 중심으로 보여 줍니다.
    $('charts-section').hidden = !overview; $('ov-section').hidden = !overview; $('requests-panel').hidden = overview;
    // 지표 카드는 전체 요청 기준이라 운영 현황에서만 보여 줍니다(오류 화면은 실패만 걸러 의미가 달라짐).
    $('metrics').hidden = !overview; $('status-strip').hidden = !overview;
    $('status-filter').disabled = view === 'errors';
    $('table-title').firstChild.textContent = view === 'errors' ? '오류 요청 ' : '요청 내역 ';
    $('export').hidden = !dataView;
    if (view === 'models') { loadModelSettings(); loadRetrain(); } else stopRetrainPolling();
    if (view === 'external') loadExternal(true); else stopExternalPolling();
    if (dataView) load();
  }

  // =========================================================
  // 검색 조건 초기화와 첫 페이지 재조회
  // =========================================================
  function resetFilters() { $('search').value = ''; $('status-filter').value = 'all'; selectPeriod('30'); }
  // =========================================================
  // 검색 지연 처리와 필터·페이지·상세 창 이벤트 연결
  // =========================================================
  let searchTimer;
  $('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.page = 1; load(); }, 250); });
  $('status-filter').addEventListener('change', () => { state.page = 1; load(); });
  document.querySelectorAll('[data-period]').forEach(button => button.addEventListener('click', () => selectPeriod(button.dataset.period)));
  ['start-date', 'end-date'].forEach(id => {
    const edited = () => {
      state.period = 'custom'; state.page = 1;
      clearTimeout(state.periodTimer); updatePeriodButtons(); disableExport();
      // 입력 중에도 이전 조회 응답이 새 날짜의 결과처럼 표시되지 않게 합니다.
      state.sequence++; state.controller?.abort(); state.loading = false; $('refresh').disabled = false;
      validDateRange();
    };
    $(id).addEventListener('input', edited);
    $(id).addEventListener('change', () => { edited(); load(); });
  });
  $('export').addEventListener('click', async event => {
    event.preventDefault();
    // 자정을 지난 상대 기간도 다시 확인하고, 화면과 같은 조건으로 내려받습니다.
    if (await load(true)) window.location.assign($('export').href);
  });
  $('clear-filters').addEventListener('click', resetFilters);
  $('refresh').addEventListener('click', () => state.view === 'models' ? (loadModelSettings(), loadRetrain()) : state.view === 'external' ? loadExternal(true) : load()); $('retry').addEventListener('click', () => load());
  $('prev-page').addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } });
  $('next-page').addEventListener('click', () => { if (state.data && state.page * state.pageSize < state.data.total) { state.page++; load(); } });
  $('request-body').addEventListener('click', event => { const b = event.target.closest('[data-request]'); if (b) showDetail(b.dataset.request); });
  $('ov-errors').addEventListener('click', event => { const b = event.target.closest('[data-request]'); if (b) showDetail(b.dataset.request); });
  document.querySelectorAll('[data-go]').forEach(b => b.addEventListener('click', () => setView(b.dataset.go)));
  document.querySelectorAll('.nav-item[data-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  $('close-detail').addEventListener('click', () => $('detail-dialog').close());
  $('detail-dialog').addEventListener('click', event => { if (event.target === $('detail-dialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
  $('detail-dialog').addEventListener('close', () => { state.selected = null; state.detailSequence++; state.previousFocus?.focus(); });
  $('auto-refresh').addEventListener('change', () => { clearInterval(state.timer); if ($('auto-refresh').checked) { state.timer = setInterval(() => { if (!document.hidden && !toolViews.includes(state.view) && !state.loading) load(true); }, 10000); toast('화면이 열려 있는 동안 10초마다 갱신합니다.'); } });
  // =========================================================
  // 화면 크기 변경에 따른 차트 갱신
  // =========================================================
  if (typeof ResizeObserver !== 'undefined') { let resizeTimer; new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.data && state.view === 'overview') drawTrend(state.data.summary.timeline); }, 100); }).observe($('trend-chart')); }

  // =========================================================
  // 구매 예측 모델 재학습: 상태 조회, 시작, 적용·버리기, 이력
  // =========================================================
  const rtLabels = { training: ['학습 중', 'running'], checking: ['자동 점검 중', 'running'], ready: ['적용 대기', 'unverified'], applied: ['적용 완료', 'completed'], discarded: ['버림', 'unknown'], failed: ['실패', 'failed'], rolled_back: ['되돌림', 'unknown'] };
  const levelLabels = { ok: '통과', warn: '경고', block: '적용 불가', info: '안내' };
  const rt = { job: null, timer: null, busy: false, seq: 0, serving: null, servingAsOf: null };
  const rtBadge = status => { const [text, cls] = rtLabels[status] || ['확인 불가', 'unknown']; return `<span class="badge ${cls}">${esc(text)}</span>`; };
  const localTime = iso => iso ? new Date(iso).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  function stopRetrainPolling() { clearTimeout(rt.timer); rt.timer = null; rt.seq++; }
  function rtFeedback(message, error = false) { $('rt-feedback').textContent = message; $('rt-feedback').classList.toggle('error', error); }

  async function postJSON(url, body) {
    const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(response.status === 401 ? '관리자 인증이 필요합니다.' : data.detail || '요청을 처리하지 못했습니다.');
      error.status = response.status; error.data = data; throw error;
    }
    return data;
  }

  function renderCurrentModel(latest, available, serving, servingAsOf) {
    // 상단 카드는 에이전트가 실제로 쓰는 모델(점수 기준일 이하 최신)을 보여 줍니다.
    const m = serving;
    rt.serving = serving; rt.servingAsOf = servingAsOf;
    const rows = !available ? [['상태', '이 프로젝트에는 구매 예측 모델 코드가 없습니다.']]
      : !m ? [['상태', latest ? `에이전트 점수 기준일(${servingAsOf}) 이하로 학습된 모델이 없습니다.` : '저장된 모델이 없습니다. 먼저 학습해 주세요.']]
      : [['기준일 (as_of)', m.as_of], ['run_id', m.run_id], ['학습 포함 조합', m.combos != null ? `${number(m.combos)}개` : '—'], ['데이터 완료일', m.complete_through || '—'], ['데이터 마지막 거래일', m.data_max || '—'], ['저장된 모델 수', `${number(m.count)}개`]];
    if (latest && (!m || latest.as_of !== m.as_of || latest.run_id !== m.run_id)) rows.push(['가장 최근 모델', `${latest.as_of} · ${latest.run_id} (에이전트 미사용)`]);
    $('rt-current').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
    if (servingAsOf) $('rt-serving-note').textContent = `에이전트는 점수 기준일 ${servingAsOf} 이하로 학습된 모델 중 가장 최근 모델을 씁니다. 더 늦은 기준일로 학습한 모델은 적용해도 쓰이지 않습니다.`;
    const active = rt.job && ['training', 'checking', 'ready'].includes(rt.job.status);
    $('rt-rollback').hidden = !(m && m.can_rollback);
    $('rt-rollback').disabled = !!active || rt.busy;
    $('rt-start').hidden = !available;
    if (m?.data_max) {
      const next = new Date(`${m.data_max}T00:00:00Z`); next.setUTCDate(next.getUTCDate() + 1);
      const max = next.toISOString().slice(0, 10);
      $('rt-asof').max = max;
      if (!$('rt-asof').value) $('rt-asof').value = m.as_of;
      $('rt-hint').textContent = `현재 모델 학습 데이터의 마지막 거래일은 ${m.data_max}이라 기준일은 최대 ${max}입니다. 에이전트는 기준일 ${servingAsOf || '—'} 이하 모델만 쓰므로 이보다 늦은 기준일은 적용해도 반영되지 않습니다. 학습은 약 2.5분, 자동 점검은 약 30초 걸리며, 학습한 모델은 [적용]을 눌러야 쓰입니다.`;
    }
  }

  function renderJob(job) {
    rt.job = job;
    $('rt-job-panel').hidden = !job;
    $('rt-job-panel').classList.toggle('highlight', job?.status === 'ready');
    const active = job && ['training', 'checking', 'ready'].includes(job.status);
    $('rt-start').disabled = !!active || rt.busy;
    if (!job) return;
    const [badgeText, badgeClass] = rtLabels[job.status] || ['확인 불가', 'unknown'];
    $('rt-badge').className = `badge ${badgeClass}`; $('rt-badge').textContent = badgeText;
    $('rt-elapsed').textContent = job.elapsed_seconds != null
      ? `경과 ${Math.floor(job.elapsed_seconds / 60)}분 ${Math.round(job.elapsed_seconds % 60)}초`
      : `시작 ${localTime(job.started_at)}${job.finished_at ? ` · 종료 ${localTime(job.finished_at)}` : ''}`;
    const at = job.status === 'failed' ? -1 : ['applied', 'discarded'].includes(job.status) ? 3 : ['training', 'checking', 'ready'].indexOf(job.status);
    const steps = ['학습', '자동 점검', '적용 결정'].map((name, i) => `<span class="${i < at ? 'done' : i === at ? 'now' : ''}">${i + 1}. ${name}</span>`).join('');
    const facts = [['기준일', job.as_of], ['데이터 완료일', job.complete_through || '마지막 거래일'], ['작업 ID', job.job_id]];
    if (job.run_id) facts.push(['새 run_id', job.run_id]);
    if (job.train?.combos != null) facts.push(['학습 포함 조합', `${number(job.train.combos)}개`]);
    if (job.train?.seconds != null) facts.push(['학습 시간', `${seconds(job.train.seconds)}초`]);
    if (job.duplicate) facts.push(['참고', '같은 기준일 모델이 이미 있습니다. 적용하면 새 모델이 쓰입니다.']);
    if (job.status === 'ready' && job.serves_agents === false) facts.push(['주의', `이 모델은 적용해도 에이전트가 쓰지 않습니다. 에이전트는 기준일 ${rt.servingAsOf || '—'} 이하 모델 중 가장 최근 모델을 씁니다.`]);
    $('rt-job').innerHTML = `<div class="retrain-steps">${steps}</div>${job.error ? `<p class="retrain-error">${esc(job.error)}</p>` : ''}<dl class="retrain-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
    const check = job.check;
    $('rt-checks').innerHTML = check
      ? `<p class="retrain-note">자동 점검 결과 · <b>${check.can_apply ? '적용할 수 있습니다' : '적용 불가 항목이 있습니다'}</b></p><table class="retrain-checks"><tbody>${(check.items || []).map(i => `<tr><td class="check-level ${esc(i.level)}">${esc(levelLabels[i.level] || i.level)}</td><td>${esc(i.title)}</td><td>${esc(i.detail)}</td></tr>`).join('')}</tbody></table>`
      : '';
    const ready = job.status === 'ready';
    $('rt-apply').hidden = !ready; $('rt-apply').disabled = !ready || !check?.can_apply || rt.busy;
    $('rt-discard').hidden = !ready; $('rt-discard').disabled = rt.busy;
    $('rt-dismiss').hidden = !['failed', 'applied', 'discarded'].includes(job.status);
    $('rt-log').textContent = (job.log_tail || []).join('\n') || '—';
  }

  function renderHistory(items) {
    $('rt-history').innerHTML = items.length
      ? items.map(h => `<tr><td>${esc(localTime(h.started_at))}</td><td>${esc(h.as_of)}</td><td>${rtBadge(h.status)}${h.error ? `<span class="request-id" title="${esc(h.error)}">${esc(h.error.slice(0, 60))}</span>` : ''}${h.restored ? `<span class="request-id">복원: ${esc(h.restored)}</span>` : ''}</td><td class="align-right">${h.combos != null ? number(h.combos) : '—'}</td><td class="align-right">${h.warnings ?? '—'}</td><td class="align-right">${h.train_seconds != null ? `${seconds(h.train_seconds)}초` : '—'}</td></tr>`).join('')
      : '<tr><td colspan="6">재학습 이력이 없습니다.</td></tr>';
  }

  async function loadRetrain() {
    stopRetrainPolling();
    const seq = rt.seq;
    try {
      const [status, history] = await Promise.all([fetchJSON('/api/retrain/status'), fetchJSON('/api/retrain/history')]);
      if (seq !== rt.seq || state.view !== 'models') return;
      rt.job = status.job;
      renderCurrentModel(status.current_model, status.available, status.serving_model, status.serving_as_of);
      renderJob(status.job);
      renderHistory(history.items);
      // 조회가 성공하면 이전의 빨간 오류 문구만 지웁니다(적용·버리기 안내는 유지).
      if ($('rt-feedback').classList.contains('error')) rtFeedback('');
      $('updated-at').textContent = `최근 갱신 ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
      if (status.job && ['training', 'checking'].includes(status.job.status)) rt.timer = setTimeout(loadRetrain, 3000);
    } catch (error) {
      if (seq === rt.seq && state.view === 'models') { rtFeedback(error.message, true); rt.timer = setTimeout(loadRetrain, 10000); }
    }
  }

  async function retrainAction(url, body, message) {
    if (rt.busy) return;
    rt.busy = true; renderJob(rt.job);
    try { await postJSON(url, body); rtFeedback(message); }
    catch (error) { rtFeedback(error.message, true); }
    finally { rt.busy = false; await loadRetrain(); }
  }

  $('rt-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (rt.busy) return;
    const body = { as_of: $('rt-asof').value, complete_through: $('rt-complete').value || null };
    rt.busy = true; $('rt-start').disabled = true; rtFeedback('학습을 시작하는 중입니다…');
    try {
      try { await postJSON('/api/retrain/start', body); }
      catch (error) {
        if (!(error.status === 409 && error.data?.duplicate)) throw error;
        if (!window.confirm(`${error.message}\n\n그래도 학습할까요?`)) { rtFeedback('학습을 시작하지 않았습니다.'); return; }
        await postJSON('/api/retrain/start', { ...body, confirm_duplicate: true });
      }
      rtFeedback('학습을 시작했습니다. 이 화면에서 진행 상태를 확인할 수 있습니다.');
    } catch (error) { rtFeedback(error.message, true); }
    finally { rt.busy = false; await loadRetrain(); }
  });
  $('rt-apply').addEventListener('click', () => {
    if (!rt.job) return;
    const serves = rt.job.serves_agents !== false;
    const effect = serves ? '적용하면 다음 점수 계산부터 이 모델이 쓰입니다.' : `주의: 이 모델은 적용해도 에이전트가 쓰지 않습니다(에이전트 기준일 ${rt.servingAsOf || '—'}).`;
    if (!window.confirm(`기준일 ${rt.job.as_of} 새 모델(run_id ${rt.job.run_id})을 적용할까요?\n${effect}`)) return;
    retrainAction('/api/retrain/apply', { job_id: rt.job.job_id }, serves ? '적용했습니다. 다음 점수 계산부터 새 모델이 쓰입니다.' : '적용했습니다. 이 모델은 에이전트 점수 계산에는 쓰이지 않습니다.');
  });
  $('rt-discard').addEventListener('click', () => {
    if (!rt.job || !window.confirm('학습한 모델을 버릴까요? 대기 폴더의 모델 파일이 삭제됩니다.')) return;
    retrainAction('/api/retrain/discard', { job_id: rt.job.job_id }, '버렸습니다. 현재 모델은 그대로입니다.');
  });
  $('rt-dismiss').addEventListener('click', () => { if (rt.job) retrainAction('/api/retrain/dismiss', { job_id: rt.job.job_id }, ''); });
  $('rt-rollback').addEventListener('click', () => {
    const m = rt.serving;
    if (!m || !window.confirm(`에이전트가 쓰는 모델(${m.as_of} · ${m.run_id})을 보관 폴더로 옮기고 이전 모델로 되돌릴까요?\n모델 파일은 삭제하지 않고 models/.retired 폴더에 보관합니다.`)) return;
    retrainAction('/api/retrain/rollback', { run_id: m.run_id }, '이전 모델로 되돌렸습니다. 다음 점수 계산부터 이전 모델이 쓰입니다.');
  });

  // =========================================================
  // 외부 데이터: 수집기 상태·수동 실행, 그래프, 미리보기
  // =========================================================
  const extRunLabels = { succeeded: ['성공', 'completed'], failed: ['실패', 'failed'], running: ['실행 중', 'running'], interrupted: ['중단됨', 'unfinished'], skipped: ['건너뜀', 'unknown'], unknown: ['확인 불가', 'unknown'] };
  const msCategory = { knit_sweater: '니트·스웨터', short_sleeve_tee: '반소매 티셔츠', long_sleeve_tee: '긴소매 티셔츠', shirt_blouse: '셔츠·블라우스', outer: '아우터' };
  const ext = { timer: null, seq: 0, busy: new Set(), naver: null, views: null, msCategory: '', msSeq: 0 };
  const extBadge = status => { const [text, cls] = extRunLabels[status] || ['기록 없음', 'unknown']; return `<span class="badge ${cls}">${esc(text)}</span>`; };
  const won = n => n == null ? '—' : `${number(Math.round(n))}원`;
  function stopExternalPolling() { clearTimeout(ext.timer); ext.timer = null; ext.seq++; }
  function extFeedback(message, error = false) { $('ext-feedback').textContent = message; $('ext-feedback').classList.toggle('error', error); }
  // 다음 조회가 성공하면 빨간 오류 문구만 지웁니다(수집 시작 같은 안내 문구는 유지).
  function clearExtError() { if ($('ext-feedback').classList.contains('error')) extFeedback(''); }

  function renderExternalCards(data) {
    // 다시 그려도 사용자가 펼치거나 접은 로그 상태를 유지합니다(처음 보이는 로그는 실행 중일 때만 펼침).
    const logOpen = {};
    $('ext-cards').querySelectorAll('details[data-ext-log]').forEach(el => { logOpen[el.dataset.extLog] = el.open; });
    $('ext-cards').innerHTML = data.collectors.map(c => {
      const last = c.last_run, job = c.job, d = c.data;
      const freshness = !d.exists ? '<span class="ext-warn">데이터 파일 없음</span>'
        : `${esc(d.collected)} <small>(${c.days_old === 0 ? '오늘' : `${c.days_old}일 전`})</small>${c.stale ? ` <span class="ext-warn">${c.stale_days}일 넘게 지남</span>` : ''}`;
      const facts = [['예약', c.schedule], ['마지막 실행', last ? `${localTime(last.started_at)} · ${last.source}` : '기록 없음'], ['다음 예약', c.next_run ? localTime(c.next_run) : (data.scheduler === 'available' ? '예약 없음' : '확인 불가')], ['최신 데이터', freshness], ...(d.facts || [])];
      const running = c.running || ext.busy.has(c.key);
      const tail = job?.log_tail?.length ? job.log_tail : last?.log_tail;
      const error = job?.error || (last?.status === 'failed' && last.error);
      return `<article class="panel retrain-card ext-card${c.stale ? ' stale' : ''}">
        <div class="retrain-job-head"><h2>${esc(c.title)}</h2>${extBadge(running ? 'running' : last?.status)}</div>
        <dl class="retrain-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${k === '최신 데이터' ? v : esc(v)}</dd>`).join('')}</dl>
        ${error ? `<p class="retrain-error">${esc(error)}</p>` : ''}
        <div class="retrain-actions"><button class="button primary" data-ext-run="${esc(c.key)}" data-ext-title="${esc(c.title)}" data-ext-duration="${esc(c.duration)}" ${running ? 'disabled' : ''}>${running ? '실행 중…' : '지금 수집'}</button><span class="retrain-note">${esc(c.script)} · ${esc(c.duration)}</span></div>
        ${tail?.length ? `<details class="retrain-log" data-ext-log="${esc(c.key)}"${(Object.hasOwn(logOpen, c.key) ? logOpen[c.key] : running) ? ' open' : ''}><summary>${running ? '실행 로그 (진행 중)' : '마지막 실행 로그'}</summary><pre class="code-block">${esc(tail.join('\n'))}</pre></details>` : ''}
      </article>`;
    }).join('');
  }

  // 일별 값 한 줄을 SVG로 그립니다. 값이 하루뿐이면 점과 값만 표시합니다.
  function drawSeries(containerId, dates, values, unit) {
    const container = $(containerId);
    const points = dates.map((date, i) => ({ date, value: values[i] })).filter(p => p.value != null);
    if (!points.length) { container.innerHTML = '<div class="chart-empty">표시할 데이터가 없습니다.</div>'; return; }
    const w = Math.max(260, container.clientWidth), h = 210, left = 46, right = 10, top = 12, bottom = 26;
    // 네이버 지수(최대 100)는 100을 위끝으로, 조회수는 최대값보다 조금 높게 잡습니다.
    const max = Math.max(...points.map(p => p.value)), ceiling = unit === '' && max <= 100 ? 100 : max > 0 ? max * 1.1 : 1;
    const x = i => points.length === 1 ? (left + w - right) / 2 : left + i / (points.length - 1) * (w - left - right);
    const y = v => h - bottom - v / ceiling * (h - bottom - top);
    const short = v => v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : `${Math.round(v)}`;
    const ticks = Array.from({ length: 5 }, (_, i) => ceiling * i / 4);
    const step = Math.max(1, Math.ceil(points.length / (w < 420 ? 4 : 6)));
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    // 점은 데이터가 적을 때만 찍고, 마지막 값은 직접 표시합니다.
    const lastI = points.length - 1;
    const dots = points.length <= 40 ? points.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.value)}" r="4" fill="${CHART.line}" stroke="#fff" stroke-width="2"/>`).join('') : `<circle cx="${x(lastI)}" cy="${y(points[lastI].value)}" r="4" fill="${CHART.line}" stroke="#fff" stroke-width="2"/>`;
    const lastLabel = `<text x="${x(lastI) - 6}" y="${y(points[lastI].value) - 9}" text-anchor="end" font-size="12" font-weight="600" fill="#1c2722">${esc(short(points[lastI].value))}${esc(unit)}</text>`;
    container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(points.map(p => `${p.date} ${p.value}`).slice(-30).join(', '))}">`
      + ticks.map(v => `<line x1="${left}" y1="${y(v)}" x2="${w - right}" y2="${y(v)}" stroke="${CHART.grid}"/><text x="${left - 8}" y="${y(v) + 4}" text-anchor="end" fill="${CHART.axis}" font-size="11">${short(v)}</text>`).join('')
      + `<line class="hover-guide" x1="0" y1="${top}" x2="0" y2="${h - bottom}" stroke="#9fb6a9" stroke-dasharray="3 3" style="display:none"/>`
      + (points.length > 1 ? `<path d="${path}" fill="none" stroke="${CHART.line}" stroke-width="2" stroke-linejoin="round"/>` : '') + dots + (points.length <= 40 ? lastLabel : '')
      + points.map((p, i) => (i % step === 0 && lastI - i >= step / 2) || i === lastI ? `<text x="${x(i)}" y="${h - 6}" text-anchor="${points.length === 1 ? 'middle' : i === 0 ? 'start' : i === lastI ? 'end' : 'middle'}" font-size="11" fill="${CHART.axis}">${esc(p.date.slice(5).replace('-', '.'))}</text>` : '').join('')
      + '</svg>';
    attachHover(container, points, x, p => `<b>${esc(p.date)}</b><br>${esc(number(p.value))}${esc(unit)}`);
  }

  // 긴 표는 처음 PREVIEW_ROWS줄만 보여 주고 '더 보기'로 나머지를 펼칩니다.
  const PREVIEW_ROWS = 10;
  function setMore(buttonId, bodyId, total) {
    const button = $(buttonId);
    button.hidden = total <= PREVIEW_ROWS;
    button.textContent = `더 보기 (${total - PREVIEW_ROWS}개)`;
    button.onclick = () => {
      const rows = $(bodyId).querySelectorAll('.extra-row'), open = rows[0]?.hidden;
      rows.forEach(r => { r.hidden = !open; });
      button.textContent = open ? '접기' : `더 보기 (${total - PREVIEW_ROWS}개)`;
    };
  }

  function fillSelect(id, options, current) {
    const select = $(id);
    select.replaceChildren(...options.map(([value, text]) => { const o = document.createElement('option'); o.value = value; o.textContent = text; o.selected = value === current; return o; }));
    select.disabled = !options.length;
    return select.value;
  }
  function drawNaver() { const d = ext.naver; if (d) drawSeries('ext-naver-chart', d.dates, d.series[$('ext-naver-kw').value] || [], ''); }
  function drawViews() { const d = ext.views; if (d) drawSeries('ext-yv-chart', d.dates, d.series[$('ext-yv-kw').value] || [], '회'); }

  async function loadMusinsa(category) {
    // 카테고리를 빠르게 바꿔도 마지막 선택의 응답만 표에 반영합니다.
    const seq = ++ext.msSeq;
    try {
      const m = await fetchJSON(`/api/external/musinsa?${new URLSearchParams({ category: category || '' })}`);
      if (seq !== ext.msSeq) return;
      ext.msCategory = fillSelect('ext-ms-cat', m.categories.map(c => [c, msCategory[c] || c]), m.category);
      $('ext-ms-note').textContent = m.date ? `${m.date} 수집 · ${msCategory[m.category] || m.category} 상위 ${m.items.length}개` : '수집된 무신사 데이터가 없습니다.';
      $('ext-ms-body').innerHTML = m.items.length ? m.items.map((r, i) => `<tr${i >= PREVIEW_ROWS ? ' class="extra-row" hidden' : ''}><td class="align-right">${r.ranking ?? '—'}</td><td class="ext-name"><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.name)}</a><span class="request-id">${esc(r.brand)}</span></td><td class="align-right">${won(r.price)}</td><td class="align-right">${r.discount_rate ? `${r.discount_rate}%` : '—'}</td><td>${esc(r.product_gender)}</td><td class="align-right">${r.rating ? r.rating.toFixed(1) : '—'}</td></tr>`).join('') : '<tr><td colspan="6">데이터가 없습니다.</td></tr>';
      setMore('ext-ms-more', 'ext-ms-body', m.items.length);
    } catch (error) { if (seq === ext.msSeq) { $('ext-ms-note').textContent = `불러오지 못했습니다: ${error.message}`; $('ext-ms-body').innerHTML = '<tr><td colspan="6">—</td></tr>'; } }
  }

  // full=true면 그래프·표까지 다시 읽고, 아니면 수집 상태 카드만 갱신합니다(실행 중 폴링).
  async function loadExternal(full = false) {
    stopExternalPolling();
    const seq = ext.seq;
    try {
      const status = await fetchJSON('/api/external/status');
      if (seq !== ext.seq || state.view !== 'external') return;
      renderExternalCards(status);
      clearExtError();
      if (full) {
        // 그래프·표는 각각 따로 처리해, 하나가 실패해도 나머지는 표시합니다.
        const failed = message => `<div class="chart-empty">불러오지 못했습니다: ${esc(message)}</div>`;
        const [naver, views, cand] = await Promise.allSettled([fetchJSON('/api/external/naver'), fetchJSON('/api/external/youtube-views'), fetchJSON('/api/external/youtube-candidates')]);
        if (seq !== ext.seq || state.view !== 'external') return;
        if (naver.status === 'fulfilled') {
          ext.naver = naver.value;
          fillSelect('ext-naver-kw', ext.naver.keywords.map(k => [k, k]), $('ext-naver-kw').value || ext.naver.keywords[0]);
          drawNaver();
        } else { ext.naver = null; $('ext-naver-chart').innerHTML = failed(naver.reason.message); }
        if (views.status === 'fulfilled') {
          ext.views = views.value;
          fillSelect('ext-yv-kw', ext.views.keywords.map(k => [k, k]), $('ext-yv-kw').value || ext.views.keywords[0]);
          drawViews();
        } else { ext.views = null; $('ext-yv-chart').innerHTML = failed(views.reason.message); }
        if (cand.status === 'fulfilled') {
          const c = cand.value;
          $('ext-yc-count').textContent = number(c.total);
          $('ext-yc-note').textContent = c.date ? `${c.date} 수집 · 등장 채널 수 상위 ${c.items.length}개 (전체 ${number(c.total)}개)` : '수집된 키워드 후보가 없습니다.';
          $('ext-yc-body').innerHTML = c.items.length ? c.items.map((r, i) => `<tr${i >= PREVIEW_ROWS ? ' class="extra-row" hidden' : ''}><td>${i + 1}</td><td>${esc(r.keyword)}</td><td>${esc(r.category)}</td><td class="align-right">${r.channels == null ? '—' : number(r.channels)}</td><td>${r.link ? `<a href="${esc(r.link)}" target="_blank" rel="noopener noreferrer">영상 ↗</a>` : ''}</td></tr>`).join('') : '<tr><td colspan="5">데이터가 없습니다.</td></tr>';
          setMore('ext-yc-more', 'ext-yc-body', c.items.length);
        } else { $('ext-yc-note').textContent = `불러오지 못했습니다: ${cand.reason.message}`; $('ext-yc-body').innerHTML = '<tr><td colspan="5">—</td></tr>'; }
        await loadMusinsa(ext.msCategory);
      }
      $('updated-at').textContent = `최근 갱신 ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
      const running = status.collectors.some(c => c.running);
      // 실행 중이면 4초마다 상태만, 아니면 1분마다 상태를 다시 확인합니다.
      ext.timer = setTimeout(() => loadExternal(false), running ? 4000 : 60000);
      if (!running && ext.wasRunning) loadExternal(true);
      ext.wasRunning = running;
    } catch (error) {
      if (seq === ext.seq && state.view === 'external') { extFeedback(error.message, true); ext.timer = setTimeout(() => loadExternal(false), 15000); }
    }
  }

  $('ext-cards').addEventListener('click', async event => {
    const button = event.target.closest('[data-ext-run]');
    if (!button) return;
    const key = button.dataset.extRun, title = button.dataset.extTitle;
    const warn = key === 'musinsa' ? '\n\n무신사 수집은 1시간 이상 걸리고, 그동안 이 PC의 크롬이 백그라운드에서 실행됩니다.' : '\n\nAPI를 사용하며, 같은 날 다시 실행하면 그날 데이터가 새 값으로 바뀝니다.';
    if (!window.confirm(`'${title}' 수집을 지금 실행할까요? (${button.dataset.extDuration})${warn}`)) return;
    ext.busy.add(key); button.disabled = true; button.textContent = '시작하는 중…';
    try { await postJSON('/api/external/run', { collector: key }); extFeedback(`'${title}' 수집을 시작했습니다. 이 화면에서 진행 상태를 확인할 수 있습니다.`); }
    catch (error) { extFeedback(error.message, true); }
    finally { ext.busy.delete(key); ext.wasRunning = true; loadExternal(false); }
  });
  $('ext-naver-kw').addEventListener('change', drawNaver);
  $('ext-yv-kw').addEventListener('change', drawViews);
  $('ext-ms-cat').addEventListener('change', () => loadMusinsa($('ext-ms-cat').value));
  if (typeof ResizeObserver !== 'undefined') { let extResize; new ResizeObserver(() => { clearTimeout(extResize); extResize = setTimeout(() => { if (state.view === 'external') { drawNaver(); drawViews(); } }, 100); }).observe($('ext-naver-chart')); }

  // =========================================================
  // 화면 진입 시 모델 설정과 최초 요청 목록 조회
  // =========================================================
  setView('overview');
})();
