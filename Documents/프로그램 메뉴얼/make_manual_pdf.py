"""
KBS 16채널 비디오 모니터링 시스템
운용 매뉴얼 PDF 생성 스크립트

사용 전 준비: 바인더1.pdf(주석 달린 캡처 PDF)를 이 스크립트와 같은 폴더에 복사하세요.
사용법: python make_manual_pdf.py
결과물: KBS_모니터링시스템_운용매뉴얼.pdf
"""

import io
import sys
import tempfile

# Windows 터미널 CP949 인코딩 문제 방지
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 경로 ──────────────────────────────────────────────────────────────────
# 실행 방법:
#   python make_manual_pdf.py                  → 기본 파일(바인더1.pdf) 사용
#   python make_manual_pdf.py 캡처본.pdf        → 지정 파일 사용
#   python make_manual_pdf.py 캡처본.pdf 결과.pdf → 입력/출력 모두 지정
BASE_DIR   = Path(__file__).parent
SRC_PDF    = BASE_DIR / (sys.argv[1] if len(sys.argv) > 1 else "바인더1.pdf")
OUTPUT_PDF = BASE_DIR / (sys.argv[2] if len(sys.argv) > 2 else "KBS_모니터링시스템_운용매뉴얼.pdf")

# ── 폰트 등록 ─────────────────────────────────────────────────────────────
pdfmetrics.registerFont(TTFont('MG',  'C:/Windows/Fonts/malgun.ttf'))
pdfmetrics.registerFont(TTFont('MGB', 'C:/Windows/Fonts/malgunbd.ttf'))

# ── 색상 ──────────────────────────────────────────────────────────────────
C_HDR  = colors.HexColor('#1a2744')
C_HDR2 = colors.HexColor('#2e4a8e')
C_SUB  = colors.HexColor('#d9e3f7')
C_BDR  = colors.HexColor('#8fa8d4')
C_ALT  = colors.HexColor('#f4f7fd')
C_BLK  = colors.HexColor('#1a1a1a')
C_RED  = colors.HexColor('#c0392b')
C_WHT  = colors.white

# ── 스타일 ────────────────────────────────────────────────────────────────
def sty(name, **kw):
    d = dict(fontName='MG', fontSize=9, leading=14, textColor=C_BLK)
    d.update(kw)
    return ParagraphStyle(name, **d)

S = {
    'cover_title': sty('ct',  fontName='MGB', fontSize=20, leading=28,
                        textColor=C_WHT, alignment=1),
    'cover_sub':   sty('cs',  fontSize=11, leading=16,
                        textColor=colors.HexColor('#c8d8f8'), alignment=1),
    'sec_hdr':     sty('sh',  fontName='MGB', fontSize=12, leading=18,
                        textColor=C_WHT),
    'page_title':  sty('pt',  fontName='MGB', fontSize=14, leading=20,
                        textColor=C_BLK),
    'desc':        sty('dc',  fontSize=9, leading=15,
                        textColor=colors.HexColor('#444444')),
    'body':        sty('bd',  fontSize=9, leading=14),
    'body_sm':     sty('bs',  fontSize=8, leading=13),
    'bold':        sty('bl',  fontName='MGB', fontSize=9, leading=14),
    'num':         sty('nm',  fontName='MGB', fontSize=11, leading=14,
                        textColor=C_RED, alignment=1),
    'tbl_hdr':     sty('th',  fontName='MGB', fontSize=9, leading=13,
                        textColor=C_BLK, alignment=1),
    'toc':         sty('tc',  fontSize=10, leading=18),
    'note':        sty('nt',  fontSize=7, leading=11,
                        textColor=colors.HexColor('#666666')),
}

def p(text, style='body'):
    # ReportLab Paragraph는 \n을 무시하므로 <br/>로 변환
    text = text.replace('\n', '<br/>')
    return Paragraph(text, S[style])

# ── reportlab 헬퍼 ────────────────────────────────────────────────────────
def sec_header(text, W):
    t = Table([[p(text, 'sec_hdr')]], colWidths=[W])
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_HDR2),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING',   (0, 0), (-1, -1), 10),
    ]))
    return t

def ann_table(data, W):
    """번호 - 항목명 - 설명 테이블
    data: [(num, name, desc), ...]
    """
    rows = [[p('번호', 'tbl_hdr'), p('항목', 'tbl_hdr'), p('설명', 'tbl_hdr')]]
    for num, name, desc in data:
        rows.append([
            p(str(num), 'num'),
            p(name, 'bold'),
            p(desc, 'body_sm'),
        ])
    cw = [11*mm, 40*mm, W - 51*mm - 10*mm]
    t = Table(rows, colWidths=cw)
    t.setStyle(TableStyle([
        ('FONTNAME',      (0, 0), (-1, -1), 'MG'),
        ('FONTSIZE',      (0, 0), (-1, -1), 9),
        ('LEADING',       (0, 0), (-1, -1), 14),
        ('GRID',          (0, 0), (-1, -1), 0.4, C_BDR),
        ('BACKGROUND',    (0, 0), (-1,  0), C_SUB),
        ('FONTNAME',      (0, 0), (-1,  0), 'MGB'),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [C_WHT, C_ALT]),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ('ALIGN',         (0, 0), ( 0, -1), 'CENTER'),
    ]))
    return t

# ── 화면별 설명 데이터 ────────────────────────────────────────────────────
# (번호, "항목명", "설명")
PAGES = [
    # ─────────────────────────────────────────
    # 1. 메인 모니터링 화면 (홈화면)
    # ─────────────────────────────────────────
    {
        "title": "1. 메인 모니터링 화면",
        "desc": (
            "프로그램 실행 시 표시되는 기본 화면입니다. "
            "상단 제어 바, 비디오 모니터링 그리드, 우측 시스템 로그 패널로 구성됩니다."
        ),
        "ann": [
            ( 1, "시스템 성능",
              "CPU·RAM·GPU 사용률을 실시간으로 표시합니다. "
              "감지 연산 부하가 높을수록 CPU 수치가 상승합니다."),

            ( 2, "현재 시간",
              "시스템 시계 기준 현재 시각(HH:MM:SS)을 표시합니다."),

            ( 3, "Embedded Audio",
              "캡처 보드로 입력되는 임베디드 오디오의 L(좌)/R(우) 레벨미터를 실시간으로 표시합니다. "
              "무음 이상 감지 시 빨간색으로 표시됩니다."),

            ( 4, "감지현황",
              "감지 이상 발생 합계를 표시합니다. "
              "V(영상: 블랙·스틸), A(오디오 레벨미터), EA(임베디드 오디오) 각각의 "
              "현재 이상 감지 채널 수를 나타냅니다."),

            ( 5, "감지 ON",
              "클릭 시 모든 감지 기능(블랙·스틸·오디오레벨미터·임베디드오디오)을 "
              "일시 중단하거나 재개합니다. 버튼 색상으로 현재 감지 활성 상태를 확인합니다."),

            ( 6, "감지영역",
              "설정 다이얼로그의 비디오·오디오 감지영역(ROI) 편집 탭을 직접 엽니다."),

            ( 7, "Mute",
              "알림음을 음소거합니다. 시각적 알림(빨간 테두리)은 음소거 중에도 유지됩니다."),

            ( 8, "알림확인",
              "현재 발생 중인 알림을 확인(해제) 처리합니다. "
              "이상 상태가 해소된 채널의 빨간 테두리가 사라집니다."),

            ( 9, "1TV / 2TV 정파",
              "1TV·2TV 그룹의 현재 정파 상태와 정파준비까지 남은 시간을 표시합니다. "
              "정파모드 진입 시 해당 그룹의 감지 알림이 자동으로 억제됩니다."),

            (10, "설정",
              "설정 다이얼로그(영상설정·비디오 영역설정·오디오 레벨미터 영역설정·"
              "감도설정·정파설정·알림설정·저장/불러오기)를 엽니다."),

            (11, "다크모드",
              "UI 테마를 다크 모드와 라이트 모드로 전환합니다."),

            (12, "전체화면",
              "프로그램 창을 전체화면 모드로 전환하거나 복원합니다."),

            (13, "Log 폴더로 이동",
              "로그 파일이 저장된 폴더를 탐색기로 엽니다. "
              "로그 파일은 날짜별로 자동 생성됩니다."),

            (14, "Log 초기화",
              "화면에 표시된 로그 내용을 지웁니다. "
              "로그 파일은 삭제되지 않으며 화면 표시만 초기화됩니다."),

            (15, "Log 창",
              "감지 이벤트, 상태 변경 등 시스템 로그를 실시간으로 표시합니다. "
              "최대 500줄을 유지하며 이상 유형별로 색상을 구분합니다. "
              "(블랙=빨강, 스틸=보라, 오디오레벨미터=초록, 임베디드오디오=파랑)"),

            (16, "입력 영상",
              "모니터링 중인 비디오 채널의 실시간 영상을 표시합니다. "
              "이상 감지 시 해당 채널에 빨간 테두리가 표시되며, "
              "각 채널 하단에 채널명이 표기됩니다."),
        ],
    },
    # ─────────────────────────────────────────
    # 2. 영상설정
    # ─────────────────────────────────────────
    {
        "title": "2. 설정 — 영상설정 탭",
        "desc": (
            "비디오 캡처 포트, 자동 녹화 기능, 녹화 파일 관리 옵션을 설정합니다."
        ),
        "ann": [
            ( 1, "캡처 포트",
              "비디오 캡처 장치의 포트 번호를 입력합니다. "
              "0 = 첫 번째 연결된 장치. 장치가 여러 개인 경우 번호를 변경하여 선택합니다. (기본값: 0)"),

            ( 2, "파일 입력 (테스트용)",
              "MP4 파일을 선택하면 실제 캡처 보드 대신 영상 파일을 소스로 사용합니다. "
              "테스트 또는 녹화 재생 목적으로 활용합니다. "
              "초기화 버튼으로 캡처 포트 소스로 복원합니다."),

            ( 3, "자동 녹화 설정",
              "체크 시 알람 발생 시점을 기준으로 전후 구간을 자동으로 MP4 파일로 녹화합니다. "
              "ffmpeg가 설치된 경우 임베디드 오디오 트랙도 함께 녹화됩니다. "
              "저장 폴더를 지정하고 '폴더 열기'로 녹화 파일을 확인합니다."),

            ( 4, "녹화 구간",
              "알람 발생 기준으로 '사고 전 버퍼(초)'만큼 이전부터, "
              "'사고 후 녹화(초)'만큼 이후까지 자동 녹화합니다. "
              "(사고 전: 1~30초, 기본 5초 / 사고 후: 1~60초, 기본 15초)"),

            ( 5, "파일 관리",
              "녹화 파일 최대 보관 기간을 설정합니다. "
              "지정 일수가 지난 파일은 자동으로 삭제됩니다. (1~365일, 기본 7일)"),

            ( 6, "녹화 출력 설정",
              "녹화 파일의 출력 해상도(기본: 960×540)와 FPS(기본: 10fps)를 설정합니다. "
              "하단 상태 표시줄에서 예상 버퍼 메모리 사용량과 녹화 파일 크기를 확인할 수 있습니다."),
        ],
    },
    # ─────────────────────────────────────────
    # 3. 비디오 영역설정
    # ─────────────────────────────────────────
    {
        "title": "3. 설정 — 비디오 감지영역 설정 탭",
        "desc": (
            "블랙·스틸 감지를 수행할 비디오 감지영역(ROI)을 설정합니다. "
            "각 영역에 라벨(V1·V2…)과 매채명을 지정합니다."
        ),
        "ann": [
            ( 1, "비디오 감지영역 편집",
              "클릭하면 반화면 ROI 편집 캔버스가 열립니다.\n"
              "• 빈 곳 클릭 드래그: 새 감지영역 생성\n"
              "• 영역 드래그: 이동 / Shift+드래그: 수직·수평 이동\n"
              "• 방향키: 10px 이동 / Shift+방향키: 1px 이동\n"
              "• Ctrl+방향키: 영역 크기 10px 조정\n"
              "• Ctrl+클릭: 범위 다중 선택 / Ctrl+클릭(선택 상태): 선택 추가·제거"),

            ( 2, "감지영역 List",
              "등록된 비디오 감지영역(ROI) 목록을 표시합니다. "
              "라벨(V1·V2…), 매채명, X·Y 좌표, W·H 크기를 확인합니다. "
              "행을 클릭하면 편집 캔버스에서 해당 영역이 선택됩니다. "
              "하단의 추가·삭제·위로·아래로·전체초기화 버튼으로 목록을 관리합니다."),
        ],
    },
    # ─────────────────────────────────────────
    # 4. 오디오 레벨미터 영역설정
    # ─────────────────────────────────────────
    {
        "title": "4. 설정 — 오디오 레벨미터 감지영역 설정 탭",
        "desc": (
            "오디오 레벨미터 색상 감지를 수행할 영역(ROI)을 설정합니다. "
            "HSV 색상 분석으로 레벨미터 활성 여부를 감지하며, 라벨은 A1·A2… 순으로 부여됩니다."
        ),
        "ann": [
            ( 1, "오디오 감지영역 편집",
              "클릭하면 ROI 편집 캔버스가 열립니다. "
              "화면 내 오디오 레벨미터 위치를 드래그로 지정합니다. "
              "편집 방법은 비디오 감지영역 편집과 동일합니다."),

            ( 2, "감지영역 List",
              "등록된 오디오 레벨미터 감지영역 목록을 표시합니다. "
              "라벨(A1·A2…), 매채명, X·Y·W·H 좌표를 확인합니다. "
              "하단의 추가·삭제·위로·아래로·전체초기화 버튼으로 목록을 관리합니다."),
        ],
    },
    # ─────────────────────────────────────────
    # 5. 감도설정
    # ─────────────────────────────────────────
    {
        "title": "5. 설정 — 감도설정 탭",
        "desc": (
            "블랙·스틸·오디오레벨미터·임베디드오디오 각 감지 기능의 민감도 파라미터와 "
            "시스템 성능을 설정합니다."
        ),
        "ann": [
            ( 1, "블랙 감지",
              "블랙화면(화면이 어두워짐) 감지 파라미터를 설정합니다.\n"
              "• 밝기 임계값(기본:10): 이 값 이하 픽셀을 '어두운 픽셀'로 판단\n"
              "• 어두운 픽셀 비율%(기본:95%): 이 비율 이상이면 블랙으로 판단\n"
              "• 알림 발생 기준(기본:20초): 블랙 상태가 이 시간 이상 지속되면 알림 발생\n"
              "• 알림 지속시간(기본:60초): 알림음을 재생하는 시간"),

            ( 2, "스틸 감지",
              "정지화면(영상이 멈춤) 감지 파라미터를 설정합니다.\n"
              "• 픽셀당 변화 기준값(기본:8): 프레임 간 픽셀 밝기 차이 임계값\n"
              "• 변화 픽셀 비율%(기본:2.0%): 이 비율 미만이면 스틸로 판단\n"
              "• 알림 발생 기준(기본:60초): 스틸 상태 지속 시 알림 발생\n"
              "• 알림 지속시간(기본:60초)"),

            ( 3, "오디오 레벨미터 감지 (HSV)",
              "오디오 레벨미터 색상 감지 파라미터를 설정합니다.\n"
              "• H·S·V 범위 슬라이더: 레벨미터 활성 색상 범위를 지정 (두 핸들 드래그)\n"
              "• 감지 픽셀 비율%(기본:5%): HSV 범위 내 픽셀이 이 비율 이상이면 활성으로 판단\n"
              "• 알림 발생 기준(기본:20초): 레벨미터 비활성 지속 시 알림 발생\n"
              "• 알림 지속시간(기본:60초)\n"
              "• 복구 딜레이(기본:2초): 알림 후 정상 복구 판정을 위한 최소 정상 지속 시간"),

            ( 4, "임베디드 오디오 감지 (무음 감지)",
              "임베디드 오디오 무음 감지 파라미터를 설정합니다.\n"
              "• 무음 임계값(기본:-50dB): 이 값 이하면 무음으로 판단 (-60~0dB)\n"
              "• 알림 발생 기준(기본:20초): 무음 상태 지속 시 알림 발생\n"
              "• 알림 지속시간(기본:60초)"),

            ( 5, "성능 설정",
              "감지 연산 성능을 조정합니다.\n"
              "• 감지 주기(기본:200ms): 값이 낮을수록 CPU 부하 증가, 10초 알림 기준 500ms까지 안전\n"
              "• 감지 해상도(기본:원본 1920×1080): 50% 축소 시 픽셀 75% 감소, 감지 정확도는 유지\n"
              "• 각 감지 기능(블랙·스틸·오디오레벨미터·임베디드오디오) 활성화 토글\n"
              "• 자동 성능 감지: 현재 시스템 성능을 분석해 최적 설정 자동 적용\n"
              "• 성능 설정 안내: 각 설정이 성능에 미치는 영향을 안내"),
        ],
    },
    # ─────────────────────────────────────────
    # 6. 정파설정
    # ─────────────────────────────────────────
    {
        "title": "6. 설정 — 정파설정 탭",
        "desc": (
            "방송 종료(정파) 시간대를 자동 인식하여 해당 시간 동안 특정 감지영역의 알림을 억제합니다. "
            "1TV·2TV 두 그룹을 독립적으로 설정할 수 있습니다."
        ),
        "ann": [
            ( 1, "자동 정파 설정",
              "자동정파 기능의 활성화 여부를 설정합니다. "
              "체크 시 설정된 스케줄에 따라 정파모드가 자동으로 활성화됩니다. "
              "'자동정파 안내' 버튼으로 정파모드의 동작 방식(준비→진입→해제 단계)을 확인할 수 있습니다."),

            ( 2, "Group 1",
              "첫 번째 정파 그룹(예: 1TV)의 스케줄을 설정합니다.\n"
              "• 그룹명: 상단바 정파 상태 표시에 사용되는 이름\n"
              "• 정파모드 시작/종료 시간: '익일' 체크 시 다음날 새벽까지 설정 가능\n"
              "• 몇 분전 정파준비 활성화: 이 시간부터 알림 억제 시작 (정파준비 상태)\n"
              "• 몇 분전 정파해제준비 활성화: 정파 종료 예고 (정파해제준비 상태)\n"
              "• 몇 초 이상시 정파해제: 영상이 이 시간 이상 정상 상태여야 정파 해제\n"
              "• 요일 선택: '매일' 또는 특정 요일 지정\n"
              "• 정파 감지영역: 알림을 억제할 ROI 선택 (트리거 영역 및 억제 대상 영역)"),

            ( 3, "Group 2",
              "두 번째 정파 그룹(예: 2TV)의 스케줄을 설정합니다. "
              "Group 1과 동일한 항목으로 구성되며, 독립적으로 설정됩니다."),

            ( 4, "정파 알림음",
              "정파 상태 변화 시 재생될 알림음 파일을 지정합니다.\n"
              "• 정파준비 시작: 정파준비 상태 진입 시 재생\n"
              "• 정파모드 진입: 정파모드 시작 시 재생\n"
              "• 정파 해제: 정파 종료 후 재생\n"
              "각 항목별 파일 선택 버튼과 테스트 재생 버튼이 제공됩니다."),

            ( 5, "정파설정 전체 초기화",
              "이 탭의 모든 설정값(그룹 스케줄, 알림음 파일 등)을 기본값으로 초기화합니다."),
        ],
    },
    # ─────────────────────────────────────────
    # 7. 알림설정
    # ─────────────────────────────────────────
    {
        "title": "7. 설정 — 알림설정 탭",
        "desc": (
            "이상 감지 시 재생될 알림음 파일을 설정하고, "
            "텔레그램 봇을 통한 원격 알림 기능을 설정합니다."
        ),
        "ann": [
            ( 1, "알림음 파일 설정",
              "이상 감지 시 재생될 알림음 WAV 파일을 설정합니다.\n"
              "• 찾아보기: 탐색기에서 WAV 파일 선택\n"
              "• 초기화: 기본 알림음 파일(alarm.wav)로 복원\n"
              "• 테스트: 현재 설정된 알림음을 즉시 재생하여 확인"),

            ( 2, "텔레그램 봇 설정",
              "텔레그램을 통한 원격 알림 기능을 설정합니다.\n"
              "• 텔레그램 알림 활성화: 체크 시 감지 이벤트 발생 시 메시지 전송\n"
              "• Bot Token: 텔레그램 BotFather에서 발급받은 봇 토큰 입력\n"
              "• Chat ID: 알림을 수신할 채널 또는 그룹의 Chat ID 입력\n"
              "• 연결 테스트: 입력된 정보로 텔레그램 연결 테스트"),

            ( 3, "텔레그램 알림 옵션",
              "텔레그램으로 전송할 알림 내용을 세부 설정합니다.\n"
              "• 알림 발생 시 스냅샷 이미지 첨부: 체크 시 감지 시점 화면 캡처를 메시지에 첨부\n"
              "• 블랙 감지 알림 / 스틸 감지 알림: 각 감지 유형별 전송 여부 선택\n"
              "• 오디오 레벨미터 감지 알림 / 임베디드 오디오 감지 알림\n"
              "• 쿨다운 시간(기본:60초): 동일 감지영역에서 N초 이내 재전송을 방지하여 "
              "알림 폭주를 예방"),
        ],
    },
    # ─────────────────────────────────────────
    # 8. 저장/불러오기
    # ─────────────────────────────────────────
    {
        "title": "8. 설정 — 저장/불러오기 탭",
        "desc": (
            "현재 설정을 JSON 파일로 저장하거나, 저장된 설정 파일을 불러옵니다. "
            "프로그램 버전 정보도 확인할 수 있습니다."
        ),
        "ann": [
            ( 1, "설정 파일 저장",
              "현재 모든 설정값을 JSON 파일로 저장합니다. "
              "탐색기에서 저장 위치를 지정합니다."),

            ( 2, "설정 파일 불러오기",
              "이전에 저장한 JSON 설정 파일을 불러와 모든 설정값을 복원합니다. "
              "PC 교체 또는 재설치 후 설정을 그대로 이전할 때 활용합니다."),

            ( 3, "기본값으로 초기화",
              "모든 설정값을 프로그램 초기 기본값으로 되돌립니다. "
              "이 작업은 취소할 수 없으므로 주의가 필요합니다."),

            ( 4, "About 정보",
              "프로그램 버전, 빌드 날짜, GitHub 저장소 주소, 개발자 이메일을 표시합니다."),
        ],
    },
]

# ── PDF 페이지 렌더링 (PyMuPDF) ───────────────────────────────────────────
def render_pdf_pages(pdf_path: Path, dpi: int = 150):
    """PDF 각 페이지를 PIL Image로 반환"""
    doc = fitz.open(str(pdf_path))
    images = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    doc.close()
    return images

# ── PDF 생성 ───────────────────────────────────────────────────────────────
def build_pdf():
    if not SRC_PDF.exists():
        print(f"[오류] 소스 PDF를 찾을 수 없습니다: {SRC_PDF}")
        print("  → 바인더1.pdf를 이 스크립트와 같은 폴더에 복사한 뒤 다시 실행하세요.")
        sys.exit(1)

    print("소스 PDF 렌더링 중...")
    page_images = render_pdf_pages(SRC_PDF, dpi=150)
    print(f"  → {len(page_images)}페이지 렌더링 완료")

    if len(page_images) != len(PAGES):
        print(f"[경고] PDF 페이지 수({len(page_images)})와 "
              f"설명 데이터 수({len(PAGES)})가 다릅니다.")

    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm,  bottomMargin=12*mm,
        title='KBS 16채널 비디오 모니터링 시스템 운용 매뉴얼',
        author='KBS전주총국',
    )
    W = 186*mm   # 유효 폭 (A4 210 - margin 12*2)
    H_PAGE = 260*mm  # 유효 높이 (여유 마진 포함)
    story = []

    # ── 표지 ────────────────────────────────────────────────────────────
    cover = [
        [p('KBS 16채널 비디오 모니터링 시스템', 'cover_title')],
        [p('운  용  매  뉴  얼', 'cover_sub')],
        [p('KBS Peacock v1.5.2  |  KBS전주총국', 'cover_sub')],
    ]
    ct = Table(cover, colWidths=[W])
    ct.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_HDR),
        ('TOPPADDING',    (0, 0), (-1, -1), 22),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 22),
        ('ROWBACKGROUNDS',(0, 0), (-1, -1), [C_HDR]),
    ]))
    story.append(ct)
    story.append(Spacer(1, 8*mm))

    # 목차
    story.append(sec_header('목  차', W))
    story.append(Spacer(1, 3*mm))
    for pg in PAGES:
        story.append(p(f'  • {pg["title"]}', 'toc'))
        story.append(Spacer(1, 1*mm))
    story.append(PageBreak())

    # ── 각 화면: 이미지 페이지 → 설명 페이지 ──────────────────────────
    tmp_dir = Path(tempfile.mkdtemp())

    for i, (pg, img_pil) in enumerate(zip(PAGES, page_images)):
        print(f"처리 중 ({i+1}/{len(PAGES)}): {pg['title']}")

        # 임시 PNG 저장
        tmp_img = tmp_dir / f"page_{i:02d}.png"
        img_pil.save(tmp_img, 'PNG')

        # ── 이미지 페이지 ─────────────────────────────────────────────
        iw, ih = img_pil.size
        ratio = min(W / iw, H_PAGE / ih)
        rl_img = RLImage(str(tmp_img), width=iw * ratio, height=ih * ratio)
        story.append(rl_img)
        story.append(PageBreak())

        # ── 설명 페이지 ───────────────────────────────────────────────
        story.append(sec_header(pg["title"], W))
        story.append(Spacer(1, 2*mm))
        story.append(p(pg["desc"], 'desc'))
        story.append(Spacer(1, 4*mm))
        story.append(ann_table(pg["ann"], W))
        story.append(PageBreak())

    # 마지막 PageBreak 제거
    if story and isinstance(story[-1], PageBreak):
        story.pop()

    print("PDF 빌드 중...")
    doc.build(story)
    print(f'\nPDF 생성 완료: {OUTPUT_PDF}')


if __name__ == '__main__':
    build_pdf()
