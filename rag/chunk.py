def split_text(
        text: str,
        chunk_size: int = 700,
        overlap: int = 100
) -> list[str]:
    """
    문서를 여러 청크로 분할

    chunk_size: 청크 최대 글자 수
    overlap: 다음 청크와 겹치는 글자 수
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size는 0보다 커야 합니다.")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap은 0 이상 chunk_size 미만이어야 합니다.")


    # 띄어쓰기 기준으로 문단 분할 (앞,뒤 공백 제거)
    paragraphs = [
        paragraph.strip()
        for paragraph in text.split("\n")
        if paragraph.strip() # 빈 줄 혹은 공백만 있는 줄 제외
    ]

    chunks = []
    pending_chunk = ""

    for paragraph in paragraphs:

        # 1. 문단이 chunk_size보다 큰 경우
        if len(paragraph) > chunk_size:

            # 1) 모으던 chunk가 존재하면 그대로 확정 후 초기화 (순서가 이어져야 하므로)
            if pending_chunk:
                chunks.append(pending_chunk)
                pending_chunk = ""

            # 2) 긴 문단을 cunk_size씩 잘라 chunk로 저장
            start = 0

            while start < len(paragraph):
                end = start + chunk_size
                chunks.append(paragraph[start:end])

                if end >= len(paragraph):
                    break

                start = end - overlap # 다음 조각의 경우 오버랩만큼 겹쳐서 시작

            continue

        # 2. 문단이 chunk_size 이하인 경우
        # 1) 모으던 chunk에 문단을 붙여 본 후보 생성
        candidate = (
            f"{pending_chunk}\n{paragraph}"
            if pending_chunk
            else paragraph
        )

        # 2) 후보가 chunk_size 이하면 pending_chunk 저장
        if len(candidate) <= chunk_size:
            pending_chunk = candidate

        # 3) 후보가 chunk_size 초과하면 붙이기 전 chunk 확정 후 새 chunk 이어가기
        else:
            # 3-1) 대기 중이던 chunk 확정
            chunks.append(pending_chunk)

            # 3-2) 오버랩 + 새 문단을 대기 chunk로 저장
            max_overlap = min(overlap, chunk_size - len(paragraph) - 1) # overlap + 새 문단이 chunk_size를 넘지 않도록 overlap 길이 제한
            overlap_text = pending_chunk[-max_overlap:] if max_overlap > 0 else ""
            pending_chunk = f"{overlap_text}\n{paragraph}".strip() # overlap_text가 비면 앞 줄바꿈 제거

    # 3. 마지막까지 남은 대기 chunk 확정
    if pending_chunk:
        chunks.append(pending_chunk)

    return chunks