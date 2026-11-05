// 관리자 화면의 기록 조회·검색·상세 표시·자동 갱신과 모델 설정 조회·변경을 담당합니다.
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
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M16 16l5 5"/>'
  };
  document.querySelectorAll('[data-icon]').forEach(el => {
    el.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[el.dataset.icon] || ''}</svg>`;
  });
  // =========================================================
  // 표시 문구, 조회 상태와 HTML 문자열 보호
  // =========================================================
  const labels = { completed: '분석 완료', failed: '실패·오류', running: '처리 중', unverified: '검증 필요', unfinished: '종료 미확인', unknown: '확인 불가' };
  const deliveryLabels = { success: '전송 완료', partial_success: '일부 실패', failed: '전송 실패', unknown: '미기록' };
  const viewLabels = { overview: ['운영 현황', 'OVERVIEW', '분석 요청의 흐름과 처리 결과를 확인하세요.'], requests: ['요청 내역', 'REQUESTS', '질문부터 최종 결과까지, 요청별 처리 기록을 살펴보세요.'], errors: ['오류 모니터링', 'ERROR MONITOR', '분석 실패와 오류 신호가 남은 요청을 확인하세요.'], guide: ['연결 안내', 'GETTING STARTED', '기존 프로젝트와 안전하게 연결하는 방법을 확인하세요.'] };
  const state = { view: 'overview', page: 1, pageSize: 12, data: null, loading: false, sequence: 0, selected: null, detailSequence: 0, controller: null, timer: null, previousFocus: null, period: '30', dateContext: null, periodTimer: null };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const number = n => new Intl.NumberFormat('ko-KR').format(n);
  const seconds = n => typeof n === 'number' && Number.isFinite(n) ? n.toFixed(1) : '—';
  const badge = status => `<span class="badge ${Object.hasOwn(labels, status) ? status : 'unknown'}">${esc(labels[status] || '확인 불가')}</span>`;
  const deliveryText = status => deliveryLabels[status] || '미기록';
  const sourceText = source => ({ log: '기존 로그', telemetry: '운영 기록' }[source] || '미기록');

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
    const currentOption = modelOptions.find(item => item.provider === data.current.provider && item.model === data.current.model);
    $('llm-current').textContent = `${currentOption?.label || data.current.provider} · ${data.current.model}`;
    $('llm-choice').replaceChildren(...modelOptions.map(item => {
      const option = document.createElement('option');
      option.value = item.provider;
      option.textContent = `${item.label} · ${item.model}${item.available ? '' : ' (API 키 미설정)'}`;
      option.selected = item.provider === data.current.provider;
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
    // 응답을 받은 조회 조건을 그대로 사용해 통계·목록과 CSV의 기간을 맞춥니다.
    const exportQuery = new URLSearchParams(query);
    exportQuery.delete('page'); exportQuery.delete('page_size');
    $('export').href = `/api/export.csv?${exportQuery}`;
    $('export').removeAttribute('aria-disabled');
  }

  // =========================================================
  // 일별 요청·실패 건수를 SVG 차트로 표현
  // =========================================================
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
      const originMessage = r.origin === 'log' ? '기존 로그에 최종 답변 본문이 없습니다. 현재 로그만으로는 확인할 수 없습니다.' : r.status === 'running' ? '요청을 처리하고 있습니다. 자동 갱신을 켜면 상태를 다시 확인합니다.' : '저장된 최종 답변이 없습니다.';
      const oldOpen = quiet ? Array.from($('detail-content').querySelectorAll('details')).map(x => x.open) : [];
      $('detail-content').innerHTML = `<div class="detail-inner"><div class="detail-statuses">${badge(r.status)}<span class="badge">Slack · ${deliveryText(r.delivery_status)}</span><span class="badge">${sourceText(r.origin)}</span></div><p class="detail-question">${esc(r.question)}</p><div class="detail-meta"><span>접수 ${esc(r.started_at || '미기록')}</span><span>종료 ${esc(r.ended_at || '미기록')}</span><span>소요 ${seconds(r.duration)}${r.duration == null ? '' : '초'}</span><span>채널 ${esc(r.channel)}</span><span>사용 모델 ${esc(r.llm_provider || '미기록')} · ${esc(r.llm_model || '미기록')}</span><span>처리 로그 ${esc(r.handler_status)}</span></div>${(r.warnings || []).map(w => `<p class="detail-warning">${esc(w)}</p>`).join('')}<section class="detail-section"><h3>최종 답변</h3><div class="answer-block ${r.answer ? '' : 'absent'}">${esc(r.answer || originMessage)}</div></section><section class="detail-section"><h3>실행 흐름</h3><div class="stage-list">${r.stages.length ? r.stages.map((s, i) => `${i ? '<span class="stage-arrow">→</span>' : ''}<span class="stage">${esc(s.name)}</span>`).join('') : '<p class="detail-empty">실행 단계 기록 없음</p>'}</div></section><section class="detail-section"><h3>생성 파일</h3>${files || `<p class="detail-empty">${'요청에 연결된 파일 기록이 없거나 파일이 존재하지 않습니다.'}</p>`}</section>${r.errors?.length ? `<section class="detail-section"><h3>오류 신호</h3>${r.errors.map(e => `<p class="error-line">${esc(e)}</p>`).join('')}</section>` : ''}<details><summary>Supervisor 판단 이유</summary><pre class="code-block">${esc(r.decisions?.join('\n\n') || '판단 이유 기록 없음')}</pre></details><details><summary>생성 SQL</summary><pre class="code-block">${esc(r.sql || 'SQL 기록 없음')}</pre></details><details><summary>상세 로그 · 최근 최대 2,000줄</summary><pre class="code-block">${esc(r.logs?.map(l => `${l.time} | ${l.level} | ${l.message}`).join('\n') || '로그 기록 없음')}</pre></details></div>`;
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
  $('refresh').addEventListener('click', () => load()); $('retry').addEventListener('click', () => load());
  $('prev-page').addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } });
  $('next-page').addEventListener('click', () => { if (state.data && state.page * state.pageSize < state.data.total) { state.page++; load(); } });
  $('request-body').addEventListener('click', event => { const b = event.target.closest('[data-request]'); if (b) showDetail(b.dataset.request); });
  document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
  $('close-detail').addEventListener('click', () => $('detail-dialog').close());
  $('detail-dialog').addEventListener('click', event => { if (event.target === $('detail-dialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
  $('detail-dialog').addEventListener('close', () => { state.selected = null; state.detailSequence++; state.previousFocus?.focus(); });
  $('auto-refresh').addEventListener('change', () => { clearInterval(state.timer); if ($('auto-refresh').checked) { state.timer = setInterval(() => { if (!document.hidden && state.view !== 'guide' && !state.loading) load(true); }, 10000); toast('화면이 열려 있는 동안 10초마다 갱신합니다.'); } });
  // =========================================================
  // 화면 크기 변경에 따른 차트 갱신
  // =========================================================
  if (typeof ResizeObserver !== 'undefined') { let resizeTimer; new ResizeObserver(() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.data && state.view === 'overview') drawTrend(state.data.summary.timeline); }, 100); }).observe($('trend-chart')); }
  // =========================================================
  // 화면 진입 시 모델 설정과 최초 요청 목록 조회
  // =========================================================
  loadModelSettings();
  load();
})();
