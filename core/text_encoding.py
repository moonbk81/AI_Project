"""로그 파일 인코딩 판별.

Windows 터미널 프로그램으로 저장한 로그는 UTF-16LE(BOM 포함)로 온다. utf-8 로
읽으면 글자마다 널이 끼어서 어떤 정규식에도 걸리지 않는다.
"""


def detect_text_encoding(path) -> str:
    with open(path, 'rb') as f:
        head = f.read(4)
    if head[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return 'utf-16'
    if head[:3] == b'\xef\xbb\xbf':
        return 'utf-8-sig'
    return 'utf-8'
