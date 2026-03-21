"""
KBS 16채널 비디오 모니터링 시스템 외주 개발비 추산 PDF 생성
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 폰트 등록 ─────────────────────────────────────────────────────────────
pdfmetrics.registerFont(TTFont('MalgunGothic', 'C:/Windows/Fonts/malgun.ttf'))
pdfmetrics.registerFont(TTFont('MalgunGothicBd', 'C:/Windows/Fonts/malgunbd.ttf'))

# ── 출력 경로 ─────────────────────────────────────────────────────────────
OUTPUT = os.path.join(os.path.dirname(__file__), 'KBS_모니터링시스템_외주개발비_추산.pdf')

# ── 색상 정의 ─────────────────────────────────────────────────────────────
C_TITLE_BG   = colors.HexColor('#1a2744')   # 진한 남색
C_HEADER_BG  = colors.HexColor('#2e4a8e')   # 중간 남색
C_SUBHDR_BG  = colors.HexColor('#d9e3f7')   # 연한 파랑
C_ROW_ALT    = colors.HexColor('#f4f7fd')   # 홀수행 배경
C_ACCENT     = colors.HexColor('#c0392b')   # 강조 빨강
C_BORDER     = colors.HexColor('#8fa8d4')   # 테이블 선
C_WHITE      = colors.white
C_BLACK      = colors.HexColor('#1a1a1a')
C_HIGHLIGHT  = colors.HexColor('#fff3cd')   # 최종 합계 강조


def style(name, **kw):
    """기본 MalgunGothic 스타일 생성"""
    defaults = dict(fontName='MalgunGothic', fontSize=9, leading=14, textColor=C_BLACK)
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)


# ── 스타일 사전 ───────────────────────────────────────────────────────────
S = {
    'title'    : style('title',    fontName='MalgunGothicBd', fontSize=18, leading=26,
                        textColor=C_WHITE, alignment=1),
    'subtitle' : style('subtitle', fontName='MalgunGothic',  fontSize=11, leading=16,
                        textColor=colors.HexColor('#c8d8f8'), alignment=1),
    'date'     : style('date',     fontName='MalgunGothic',  fontSize=9,  leading=13,
                        textColor=colors.HexColor('#aabce8'), alignment=1),
    'section'  : style('section',  fontName='MalgunGothicBd', fontSize=11, leading=18,
                        textColor=C_WHITE),
    'body'     : style('body',     fontSize=9, leading=14, leftIndent=4),
    'bullet'   : style('bullet',   fontSize=9, leading=14, leftIndent=12, bulletIndent=4),
    'bold'     : style('bold',     fontName='MalgunGothicBd', fontSize=9, leading=14),
    'center'   : style('center',   fontSize=9, leading=14, alignment=1),
    'right'    : style('right',    fontSize=9, leading=14, alignment=2),
    'total_lbl': style('total_lbl', fontName='MalgunGothicBd', fontSize=13, leading=20,
                        textColor=C_ACCENT, alignment=1),
    'total_num': style('total_num', fontName='MalgunGothicBd', fontSize=16, leading=24,
                        textColor=C_ACCENT, alignment=1),
    'note'     : style('note',     fontSize=8, leading=12,
                        textColor=colors.HexColor('#555555'), leftIndent=4),
}

# ── 테이블 스타일 공통 ────────────────────────────────────────────────────
def base_table_style(extra=None):
    cmds = [
        ('FONTNAME',   (0,0), (-1,-1), 'MalgunGothic'),
        ('FONTSIZE',   (0,0), (-1,-1), 9),
        ('LEADING',    (0,0), (-1,-1), 14),
        ('TEXTCOLOR',  (0,0), (-1,-1), C_BLACK),
        ('GRID',       (0,0), (-1,-1), 0.4, C_BORDER),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [C_WHITE, C_ROW_ALT]),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
        ('RIGHTPADDING',  (0,0), (-1,-1), 6),
    ]
    if extra:
        cmds.extend(extra)
    return TableStyle(cmds)


def section_header(text):
    """진한 남색 섹션 헤더 박스"""
    tbl = Table([[Paragraph(text, S['section'])]], colWidths=[170*mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), C_HEADER_BG),
        ('TOPPADDING',    (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
    ]))
    return tbl


def p(text, sty='body'):
    return Paragraph(text, S[sty])


# ── 문서 구성 ─────────────────────────────────────────────────────────────
def build():
    doc = SimpleDocTemplate(
        OUTPUT,
        pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm,  bottomMargin=18*mm,
        title='KBS 16채널 비디오 모니터링 시스템 외주 개발비 추산',
        author='KBS 방송기술국',
    )

    story = []
    W = 170*mm  # 유효 폭

    # ── 타이틀 박스 ──────────────────────────────────────────────────────
    title_data = [
        [p('KBS 16채널 비디오 모니터링 시스템', 'title')],
        [p('외주 개발비 추산 보고서', 'subtitle')],
        [p('기준: 한국소프트웨어산업협회(KOSA) 2024년 SW기술자 평균임금 공표\n'
           '적용법령: 과학기술정보통신부 고시 「소프트웨어사업 대가기준」', 'date')],
    ]
    title_tbl = Table(title_data, colWidths=[W])
    title_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), C_TITLE_BG),
        ('TOPPADDING',    (0,0), (-1,-1), 14),
        ('BOTTOMPADDING', (0,0), (-1,-1), 14),
        ('LEFTPADDING',   (0,0), (-1,-1), 10),
        ('RIGHTPADDING',  (0,0), (-1,-1), 10),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [C_TITLE_BG]),
    ]))
    story.append(title_tbl)
    story.append(Spacer(1, 6*mm))

    # ── 1. 시스템 규모 분석 ───────────────────────────────────────────────
    story.append(section_header('1. 시스템 규모 분석'))
    story.append(Spacer(1, 2*mm))

    scale_data = [
        [p('구분', 'bold'), p('내용', 'bold'), p('수치', 'bold')],
        [p('시스템 유형'), p('방송용 실시간 비디오 모니터링 데스크톱 애플리케이션'), p('—', 'center')],
        [p('총 소스 파일'), p('Python 모듈 (UI 6 + Core 7 + Utils 3 + Config/Resources)'), p('21개', 'center')],
        [p('총 코드 라인'), p('주석·공백 제외 실효 코드 기준'), p('약 9,280줄', 'center')],
        [p('개발 버전 이력'), p('v1.01 → v1.5.2 (정식 릴리스 기준)'), p('24회 커밋', 'center')],
        [p('핵심 기술 스택'), p('PySide6(Qt6), OpenCV, sounddevice, FFmpeg, Telegram Bot API'), p('—', 'center')],
        [p('감지 기능 종류'), p('블랙화면 / 스틸화면 / 오디오레벨미터 / 임베디드오디오'), p('4종', 'center')],
        [p('UI 탭 수'), p('입력선택 / 비디오감지 / 오디오감지 / 감지설정 / 알림설정 / 저장'), p('6개', 'center')],
    ]
    cw = [32*mm, 108*mm, 30*mm]
    tbl = Table(scale_data, colWidths=cw)
    tbl.setStyle(base_table_style([
        ('BACKGROUND',  (0,0), (-1,0), C_SUBHDR_BG),
        ('FONTNAME',    (0,0), (-1,0), 'MalgunGothicBd'),
        ('ALIGN',       (0,0), (-1,0), 'CENTER'),
        ('FONTNAME',    (0,1), (0,-1), 'MalgunGothicBd'),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 5*mm))

    # ── 2. 단계별 공수 산정 ──────────────────────────────────────────────
    story.append(section_header('2. 단계별 공수 산정 (M/D: 인·일)'))
    story.append(Spacer(1, 2*mm))

    effort_data = [
        [p('개발 단계', 'bold'), p('주요 내용', 'bold'), p('등급', 'bold'), p('M/D', 'bold')],
        [p('요구사항 분석 및 설계'),
         p('기능명세서, 화면설계서, 시스템 아키텍처 설계'),
         p('고급', 'center'), p('8', 'center')],
        [p('비디오 캡처 엔진'),
         p('OpenCV CAP_DSHOW, QThread 멀티스레딩 파이프라인'),
         p('고급', 'center'), p('5', 'center')],
        [p('감지 알고리즘 개발'),
         p('블랙·스틸·오디오레벨미터·임베디드오디오 4종\nHSV 기반 색상 분석, 파라미터 튜닝'),
         p('고급', 'center'), p('15', 'center')],
        [p('커스텀 ROI 편집기'),
         p('마우스 드래그 캔버스, 풀스크린 편집 모드 (972줄)'),
         p('고급', 'center'), p('8', 'center')],
        [p('정파모드 상태머신'),
         p('방송 특화 정파·복귀 로직, 그룹별 억제, 타이머 제어 (664줄)'),
         p('고급', 'center'), p('7', 'center')],
        [p('알림 시스템'),
         p('winsound/sounddevice 멀티스레딩, 시각 깜박임 알림'),
         p('고급', 'center'), p('5', 'center')],
        [p('자동 녹화 (FFmpeg 합성)'),
         p('비디오+오디오 타임스탬프 동기화, MP4 합성 (424줄)'),
         p('고급', 'center'), p('5', 'center')],
        [p('설정 다이얼로그 6탭'),
         p('3,048줄, 복잡한 폼 레이아웃 + 입력 유효성 검증'),
         p('중급', 'center'), p('15', 'center')],
        [p('메인 윈도우 + 상단바'),
         p('3분할 레이아웃, 시스템모니터, 레벨미터, 감지현황'),
         p('중급', 'center'), p('8', 'center')],
        [p('커스텀 위젯 개발'),
         p('HSV 듀얼슬라이더, 로그 위젯, 비디오 오버레이'),
         p('중급', 'center'), p('6', 'center')],
        [p('텔레그램 연동'),
         p('Bot API 비동기 전송, 재시도 및 안정성 처리'),
         p('중급', 'center'), p('4', 'center')],
        [p('설정 저장/불러오기 + 로깅'),
         p('JSON 직렬화, 일별 로테이션 로그, 기본값 초기화'),
         p('중급', 'center'), p('5', 'center')],
        [p('통합 테스트 및 버그 수정'),
         p('실제 방송 환경 투입 테스트, v1.01→v1.5.2 반복 수정'),
         p('고급', 'center'), p('10', 'center')],
        [p('납품 문서화 및 배포 패키징'),
         p('운영 매뉴얼, 설치 가이드, 실행 파일 패키징'),
         p('중급', 'center'), p('5', 'center')],
    ]

    # 합계행
    effort_data.append([
        p('합  계', 'bold'),
        p('고급기술자 63 M/D  +  중급기술자 38 M/D', 'bold'),
        p('—', 'center'),
        p('101', 'bold'),
    ])

    cw2 = [38*mm, 95*mm, 15*mm, 22*mm]
    tbl2 = Table(effort_data, colWidths=cw2)
    tbl2.setStyle(base_table_style([
        ('BACKGROUND',  (0,0),  (-1,0),  C_SUBHDR_BG),
        ('FONTNAME',    (0,0),  (-1,0),  'MalgunGothicBd'),
        ('ALIGN',       (0,0),  (-1,0),  'CENTER'),
        ('FONTNAME',    (0,1),  (0,-1),  'MalgunGothicBd'),
        ('BACKGROUND',  (0,-1), (-1,-1), colors.HexColor('#e8eef8')),
        ('FONTNAME',    (0,-1), (-1,-1), 'MalgunGothicBd'),
        ('LINEABOVE',   (0,-1), (-1,-1), 1.0, C_HEADER_BG),
    ]))
    story.append(tbl2)
    story.append(Spacer(1, 5*mm))

    # ── 3. 노임단가 기준 ─────────────────────────────────────────────────
    story.append(section_header('3. 적용 노임단가 기준'))
    story.append(Spacer(1, 2*mm))

    story.append(p('▶ 한국소프트웨어산업협회(KOSA) 2024년 SW기술자 평균임금 공표', 'body'))
    story.append(p('▶ 「소프트웨어사업 대가기준」(과학기술정보통신부 고시) 준용', 'body'))
    story.append(Spacer(1, 2*mm))

    wage_data = [
        [p('등급', 'bold'), p('경력 기준', 'bold'), p('월 평균임금', 'bold'),
         p('일 임금 (÷21.5일)', 'bold'), p('투입 M/D', 'bold')],
        [p('고급기술자'), p('10년 이상'), p('6,282,041원', 'right'),
         p('292,188원', 'right'), p('63일', 'center')],
        [p('중급기술자'), p('5년 이상'),  p('4,916,503원', 'right'),
         p('228,582원', 'right'), p('38일', 'center')],
    ]
    cw3 = [28*mm, 28*mm, 38*mm, 42*mm, 24*mm]
    tbl3 = Table(wage_data, colWidths=cw3)
    tbl3.setStyle(base_table_style([
        ('BACKGROUND', (0,0), (-1,0), C_SUBHDR_BG),
        ('FONTNAME',   (0,0), (-1,0), 'MalgunGothicBd'),
        ('ALIGN',      (0,0), (-1,0), 'CENTER'),
        ('FONTNAME',   (0,1), (0,-1), 'MalgunGothicBd'),
    ]))
    story.append(tbl3)
    story.append(Spacer(1, 5*mm))

    # ── 4. 비용 산출 ─────────────────────────────────────────────────────
    story.append(section_header('4. 개발비 산출 내역'))
    story.append(Spacer(1, 2*mm))

    cost_data = [
        [p('항목', 'bold'), p('산출 근거', 'bold'), p('금액', 'bold')],
        [p('직접인건비 — 고급기술자'),
         p('292,188원 × 63일'),
         p('18,407,844원', 'right')],
        [p('직접인건비 — 중급기술자'),
         p('228,582원 × 38일'),
         p('8,686,116원', 'right')],
        [p('직접인건비 소계', 'bold'),
         p(''),
         p('27,093,960원', 'right')],
        [p('제경비'),
         p('직접인건비 × 110%  (간접비·관리비·보험료 등 포함)'),
         p('29,803,356원', 'right')],
        [p('기술료'),
         p('(직접인건비 + 제경비) 합계 × 25%  (기술 노하우·지식재산권 대가)'),
         p('14,224,329원', 'right')],
        [p('부가가치세'),
         p('(직접인건비 + 제경비 + 기술료) × 10%'),
         p('7,152,164원', 'right')],
    ]
    # 합계행
    cost_data.append([
        p('최종 계약 예상 금액', 'bold'),
        p('VAT 포함'),
        p('78,673,809원', 'right'),
    ])

    cw4 = [46*mm, 90*mm, 34*mm]
    tbl4 = Table(cost_data, colWidths=cw4)
    tbl4.setStyle(base_table_style([
        ('BACKGROUND',  (0,0),  (-1,0),  C_SUBHDR_BG),
        ('FONTNAME',    (0,0),  (-1,0),  'MalgunGothicBd'),
        ('ALIGN',       (0,0),  (-1,0),  'CENTER'),
        ('FONTNAME',    (0,1),  (0,-1),  'MalgunGothicBd'),
        # 소계행 구분선
        ('LINEBELOW',   (0,3),  (-1,3),  0.8, C_HEADER_BG),
        # 합계행
        ('BACKGROUND',  (0,-1), (-1,-1), C_HIGHLIGHT),
        ('FONTNAME',    (0,-1), (-1,-1), 'MalgunGothicBd'),
        ('FONTSIZE',    (0,-1), (-1,-1), 10),
        ('LINEABOVE',   (0,-1), (-1,-1), 1.2, C_ACCENT),
        ('TEXTCOLOR',   (2,-1), (2,-1),  C_ACCENT),
    ]))
    story.append(tbl4)
    story.append(Spacer(1, 5*mm))

    # ── 최종 강조 박스 ────────────────────────────────────────────────────
    summary_data = [
        [p('외주 개발 시 예상 계약 금액 (VAT 포함)', 'total_lbl')],
        [p('약 7,800만원 ~ 8,000만원', 'total_num')],
        [p('* KOSA 2024 공식 노임단가 + 과기정통부 대가기준 제경비·기술료 적용', 'note')],
    ]
    sum_tbl = Table(summary_data, colWidths=[W])
    sum_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,1), C_HIGHLIGHT),
        ('BACKGROUND',    (0,2), (-1,2), colors.HexColor('#fefefe')),
        ('TOPPADDING',    (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('BOX',           (0,0), (-1,-1), 1.5, C_ACCENT),
        ('LINEBELOW',     (0,1), (-1,1), 0.5, C_BORDER),
        ('ALIGN',         (0,0), (-1,1), 'CENTER'),
    ]))
    story.append(sum_tbl)
    story.append(Spacer(1, 5*mm))

    # ── 5. 기술적 난이도 설명 ────────────────────────────────────────────
    story.append(section_header('5. 고비용 사유 — 기술 복잡도 근거'))
    story.append(Spacer(1, 2*mm))

    reasons = [
        ('실시간 영상·오디오 처리 전문성',
         'OpenCV 기반 실시간 영상 분석과 sounddevice 오디오 처리를 PySide6 멀티스레딩 구조에 통합.'
         ' 방송·영상 도메인 전문지식 없이는 구현 불가능한 영역.'),
        ('감지 알고리즘 4종 독립 개발',
         '블랙화면·스틸화면·오디오레벨미터·임베디드오디오는 각각 별개의 신호처리 이론이 적용됨.'
         ' 단순 CRUD 시스템 대비 수배의 알고리즘 설계 공수 요구.'),
        ('방송 특화 정파모드 상태머신',
         '1TV·2TV별 정파 스케줄 인식, 진입·해제 타이머, 알림 억제 로직을 포함한 복잡한 상태 전이 설계.'
         ' 방송 운영 현장 지식이 요구되는 고난도 기능.'),
        ('커스텀 Qt 위젯 및 ROI 편집기',
         '드래그로 감지영역을 지정하는 캔버스, HSV 듀얼슬라이더 등 표준 위젯으로 대체 불가능한'
         ' 커스텀 컴포넌트를 직접 구현. 풀스크린 편집 모드 포함 (972줄).'),
        ('설정 다이얼로그 복잡도',
         '6개 탭, 3,048줄의 UI 코드. 탭당 수십 개의 입력 필드와 유효성 검증 로직 포함.'
         ' 일반 기업 설정 화면 대비 5~10배 복잡한 수준.'),
        ('현장 투입 테스트 및 반복 수정',
         'v1.01 최초 납품 후 실제 방송 현장 투입 과정에서 발생한 이슈를 v1.5.2까지 수정.'
         ' 24회 커밋이 실제 현장 검증·수정 공수의 증거.'),
    ]
    reason_data = [[p('항목', 'bold'), p('설명', 'bold')]]
    for i, (title, desc) in enumerate(reasons):
        reason_data.append([p(title, 'bold'), p(desc)])

    cw5 = [46*mm, 124*mm]
    tbl5 = Table(reason_data, colWidths=cw5)
    tbl5.setStyle(base_table_style([
        ('BACKGROUND',  (0,0),  (-1,0),  C_SUBHDR_BG),
        ('FONTNAME',    (0,0),  (-1,0),  'MalgunGothicBd'),
        ('ALIGN',       (0,0),  (-1,0),  'CENTER'),
        ('FONTNAME',    (0,1),  (0,-1),  'MalgunGothicBd'),
        ('VALIGN',      (0,0),  (-1,-1), 'TOP'),
    ]))
    story.append(tbl5)
    story.append(Spacer(1, 5*mm))

    # ── 6. 비교 참고 ──────────────────────────────────────────────────────
    story.append(section_header('6. 비교 참고 — 유사 프로젝트 외주 시장 단가'))
    story.append(Spacer(1, 2*mm))

    ref_data = [
        [p('시스템 유형', 'bold'), p('주요 특징', 'bold'), p('통상 외주 단가', 'bold')],
        [p('단순 업무 관리 웹앱'),
         p('CRUD 위주, 표준 프레임워크 사용'),
         p('1,500만 ~ 3,000만원', 'center')],
        [p('모바일 앱 (iOS/Android)'),
         p('일반 기능 앱, 백엔드 포함'),
         p('3,000만 ~ 6,000만원', 'center')],
        [p('산업용 모니터링 데스크톱 SW'),
         p('실시간 데이터 처리, 커스텀 UI'),
         p('5,000만 ~ 1억원', 'center')],
        [p('본 시스템\n(방송용 실시간 모니터링)', 'bold'),
         p('영상+오디오 실시간 처리, 4종 감지 알고리즘\n방송 특화 상태머신, 커스텀 위젯 다수', 'bold'),
         p('약 7,800만원\n(VAT 포함)', 'center')],
    ]
    cw6 = [42*mm, 90*mm, 38*mm]
    tbl6 = Table(ref_data, colWidths=cw6)
    tbl6.setStyle(base_table_style([
        ('BACKGROUND',  (0,0),  (-1,0),  C_SUBHDR_BG),
        ('FONTNAME',    (0,0),  (-1,0),  'MalgunGothicBd'),
        ('ALIGN',       (0,0),  (-1,0),  'CENTER'),
        ('FONTNAME',    (0,1),  (0,-1),  'MalgunGothicBd'),
        ('BACKGROUND',  (0,-1), (-1,-1), colors.HexColor('#e8eef8')),
        ('FONTNAME',    (0,-1), (-1,-1), 'MalgunGothicBd'),
        ('TEXTCOLOR',   (2,-1), (2,-1),  C_ACCENT),
        ('LINEABOVE',   (0,-1), (-1,-1), 1.0, C_HEADER_BG),
        ('FONTNAME',    (0,1), (0,-1), 'MalgunGothicBd'),
    ]))
    story.append(tbl6)
    story.append(Spacer(1, 5*mm))

    # ── 면책 문구 ─────────────────────────────────────────────────────────
    story.append(HRFlowable(width=W, thickness=0.5, color=C_BORDER))
    story.append(Spacer(1, 2*mm))
    notes = [
        '※ 본 추산은 KOSA 2024년 노임단가 및 과기정통부 「소프트웨어사업 대가기준」을 기반으로 산출한 참고 자료입니다.',
        '※ 실제 외주 계약 시 업체별 간접비율, 기술료율, 협상 조건에 따라 ±20% 범위 내에서 변동될 수 있습니다.',
        '※ 제경비율 110%, 기술료율 25%는 동 고시에서 정한 중간값을 적용하였습니다.',
    ]
    for note in notes:
        story.append(p(note, 'note'))
        story.append(Spacer(1, 1*mm))

    doc.build(story)
    print(f'PDF 생성 완료: {OUTPUT}')


if __name__ == '__main__':
    build()
