"""CART_SIGNAL 8단계 근거 보강: 실제 상품 데이터, 시각화, 네이버 가용 범위 표시."""
from __future__ import annotations

from pathlib import Path
import pandas as pd


def _style_tags(articles: pd.DataFrame, ids: list[str]) -> dict[str, list[str]]:
    selected = articles[articles['article_id'].astype(str).str.zfill(10).isin(ids)]
    out = {}
    for _, row in selected.iterrows():
        tags = row.get('style_tags')
        out[str(row['article_id']).zfill(10)] = list(tags) if isinstance(tags, (tuple, list, set)) else []
    return out


def _naver_available(category: str):
    """수집된 키워드만 보고; 매핑 불명 키워드는 임의 귀속하지 않는다."""
    try:
        from agent_system.common_tools.external_data import trend_files
        from run_market_category_compare import KEYWORD_CATEGORY
        frame = trend_files.load_naver()
        if frame is None or frame.empty or 'keyword' not in frame:
            return [], '네이버 배치에 분석 가능한 키워드가 없습니다.'
        collected = sorted(set(frame['keyword'].dropna().astype(str).str.strip()) - {''})
        matched = [x for x in collected if KEYWORD_CATEGORY.get(x) == category]
        unclassified = [x for x in collected if not KEYWORD_CATEGORY.get(x)]
        note = (f'네이버 수집 키워드 중 해당 카테고리 매핑: {", ".join(matched)}' if matched
                else '네이버 수집 키워드 중 해당 카테고리에 매핑된 항목이 없습니다.')
        if unclassified:
            note += f' / 카테고리 미분류 키워드 {len(unclassified)}개(매핑 확인 필요)'
        note += '. 네이버 관심도는 상품별 구매·인기 또는 키워드 직접 매칭 근거가 아닙니다.'
        return matched, note
    except (ImportError, FileNotFoundError, KeyError, ValueError) as exc:
        return [], f'네이버 배치 확인 불가 ({type(exc).__name__}); H&M 상품 검색에는 영향 없음.'


def _chart(rows: pd.DataFrame, destination: Path) -> str:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if any(f.name == 'AppleGothic' for f in font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = 'AppleGothic'
    labels = [f"{str(n)[:22]} ({a})" for n, a in zip(rows['prod_name'], rows['article_id'])]
    values = rows['winter_online_purchases'].astype(int).tolist()
    fig, ax = plt.subplots(figsize=(10, max(3.8, 0.65 * len(rows) + 1)))
    ax.barh(list(reversed(labels)), list(reversed(values)))
    ax.set_xlabel('Historical winter online purchase records (2018-2020)')
    ax.set_title('H&M candidate comparison: historical records, not current sales')
    for index, value in enumerate(reversed(values)):
        ax.text(value, index, f' {value:,}', va='center', fontsize=9)
    ax.set_xlim(0, max(values) * 1.22)
    fig.tight_layout()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=140)
    plt.close(fig)
    return str(destination.resolve())


def enrich_verified_result(result: dict, category: str, run_id: str, *, include_chart=True, include_naver=True) -> dict:
    """DB 검색 성공 후에만 적용. 검증 후보 없는 경우 8단계 게이트 결과 유지."""
    frame = result.get('dataframe')
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return result
    from agent_system.common_tools.local_data import hm_products
    from agent_system.common_tools.local_data import hm_images
    frame = frame.copy()
    frame['article_id'] = frame['article_id'].astype(str).str.zfill(10)
    ids = frame['article_id'].tolist()
    tags_by_id = _style_tags(hm_products.articles(), ids)
    frame['style_tags'] = [', '.join(tags_by_id.get(x, [])) or '(추출된 태그 없음)' for x in ids]
    image_infos = [hm_images.image_info(x) for x in ids]
    frame['image_available'] = [bool(d.get('image_available')) for d in image_infos]
    frame['image_status'] = [str(d.get('image_status') or '') for d in image_infos]
    if 'keyword_match_verified' not in frame:
        frame['keyword_match_verified'] = False
    # 거래 기록과 스타일 태그는 검증. 외부 트렌드와의 직접 일치 여부는 미검증.
    naver_note = _naver_available(category)[1] if include_naver else '네이버 관심도는 6~7단계의 별도 근거입니다.'
    lines = [result.get('final_answer', '').strip(), '', '■ 10단계 결정을 위한 추가 근거',
             'H&M 스타일 태그는 상품명·설명에서 규칙으로 추출한 속성이며 트렌드 상승 지표가 아닙니다.',
             naver_note,
             ('상품명·설명에서 선택 유형을 규칙 매칭했습니다. 실측 성능·충전재 상세는 별도 확인.' if frame['keyword_match_verified'].all() else '네이버 키워드와 실제 H&M 상품의 직접 일치는 아직 검증하지 않았습니다.'),
             '상품 이미지가 없어도 상품 ID와 과거 거래 기록은 유효할 수 있습니다.']
    for _, row in frame.iterrows():
        lines.append(f"- {row['article_id']} | 스타일 태그: {row['style_tags']} | 이미지: {'확인' if row['image_available'] else '미확인'}")
    updated = dict(result)
    updated['final_answer'] = '\n'.join(lines)
    updated['dataframe'] = frame
    updated['product_result'] = {'search_products': {'rows': frame.to_dict(orient='records')}}
    if not include_chart:
        return updated
    root = Path(__file__).resolve().parent
    chart = root / 'visualization_output' / 'category_product_evidence' / str(run_id) / f'{category}_winter_purchases.png'
    try:
        updated['visualization_path'] = _chart(frame, chart)
    except (ImportError, OSError, ValueError) as exc:
        updated['final_answer'] += f'\n구매 실적 그래프 생성 불가 ({type(exc).__name__}). 표는 유지됩니다.'
    return updated
