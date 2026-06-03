"""/code_propose 테스트용 샘플 유틸리티.

이 모듈은 /code_propose 명령어의 코드 제안 → 승인 → 머지 워크플로우를
검증하기 위한 테스트용 샘플 유틸리티입니다. 외부 의존성 없이 표준
라이브러리만을 사용하며, 기존 봇 로직과는 완전히 독립적으로 동작합니다.
"""


def hello_gaecho() -> str:
    """개쵸의 인사 메시지를 반환합니다.

    Returns:
        고정된 인사 문자열.
    """
    return '안녕하세요, 개쵸입니다!'


def add_numbers(a: int, b: int) -> int:
    """두 정수의 합을 계산합니다.

    Args:
        a: 첫 번째 정수.
        b: 두 번째 정수.

    Returns:
        두 정수의 합.
    """
    return a + b


def get_sample_info() -> dict:
    """샘플 모듈의 메타 정보를 반환합니다.

    Returns:
        모듈 이름, 버전, 용도를 담은 dict.
    """
    return {
        'name': 'test_sample',
        'version': '1.0.0',
        'purpose': 'code_propose_test',
    }


if __name__ == '__main__':
    # 각 함수의 동작을 간단히 확인
    print(hello_gaecho())
    print(add_numbers(3, 5))
    print(get_sample_info())